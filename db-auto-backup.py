#!/usr/bin/env python3
import bz2
import fnmatch
import gzip
import lzma
import os
import secrets
import sys
from datetime import datetime
from io import StringIO
from pathlib import Path
from typing import IO, Callable, Dict, Iterable, NamedTuple, Optional

import docker
import pycron
import requests
import boto3
from botocore.exceptions import ClientError
from docker.models.containers import Container
from dotenv import dotenv_values
from tqdm.auto import tqdm


class BackupProvider(NamedTuple):
    name: str
    patterns: list[str]
    backup_method: Callable[[Container], str]
    file_extension: str


def get_container_env(container: Container) -> Dict[str, Optional[str]]:
    """
    Get all environment variables from a container.

    Variables at runtime, rather than those defined in the container.
    """
    _, (env_output, _) = container.exec_run("env", demux=True)
    return dict(dotenv_values(stream=StringIO(env_output.decode())))


def binary_exists_in_container(container: Container, binary_name: str) -> bool:
    """
    Get all environment variables from a container.

    Variables at runtime, rather than those defined in the container.
    """
    exit_code, _ = container.exec_run(["which", binary_name])
    return exit_code == 0


def temp_backup_file_name() -> str:
    """
    Create a temporary file to save backups to,
    then atomically replace backup file
    """
    return ".auto-backup-" + secrets.token_hex(4)


def open_file_compressed(file_path: Path, algorithm: str) -> IO[bytes]:
    file_path.touch(mode=0o600)

    if algorithm == "gzip":
        return gzip.open(file_path, mode="wb")  # type:ignore
    elif algorithm in ["lzma", "xz"]:
        return lzma.open(file_path, mode="wb")
    elif algorithm == "bz2":
        return bz2.open(file_path, mode="wb")
    elif algorithm == "plain":
        return file_path.open(mode="wb")
    raise ValueError(f"Unknown compression method {algorithm}")


def get_compressed_file_extension(algorithm: str) -> str:
    if algorithm == "gzip":
        return ".gz"
    elif algorithm in ["lzma", "xz"]:
        return ".xz"
    elif algorithm == "bz2":
        return ".bz2"
    elif algorithm == "plain":
        return ""
    raise ValueError(f"Unknown compression method {algorithm}")


def get_success_hook_url() -> Optional[str]:
    if success_hook_url := os.environ.get("SUCCESS_HOOK_URL"):
        return success_hook_url

    if healthchecks_id := os.environ.get("HEALTHCHECKS_ID"):
        healthchecks_host = os.environ.get("HEALTHCHECKS_HOST", "hc-ping.com")
        return f"https://{healthchecks_host}/{healthchecks_id}"

    if uptime_kuma_url := os.environ.get("UPTIME_KUMA_URL"):
        return uptime_kuma_url

    return None


def backup_psql(container: Container) -> str:
    env = get_container_env(container)
    user = env.get("POSTGRES_USER", "postgres")
    return f"pg_dumpall -U {user}"


def backup_mysql(container: Container) -> str:
    env = get_container_env(container)

    # The mariadb container supports both
    if "MARIADB_ROOT_PASSWORD" in env:
        auth = "-p$MARIADB_ROOT_PASSWORD"
    elif "MYSQL_ROOT_PASSWORD" in env:
        auth = "-p$MYSQL_ROOT_PASSWORD"
    else:
        raise ValueError(f"Unable to find MySQL root password for {container.name}")

    if binary_exists_in_container(container, "mariadb-dump"):
        backup_binary = "mariadb-dump"
    else:
        backup_binary = "mysqldump"

    return f"bash -c '{backup_binary} {auth} --all-databases'"


def backup_redis(container: Container) -> str:
    """
    Note: `SAVE` command locks the database, which isn't ideal.
    Hopefully the commit is fast enough!
    """
    return "sh -c 'redis-cli SAVE > /dev/null && cat /data/dump.rdb'"


def upload_to_s3(file_path: Path, s3_key: str) -> bool:
    """
    Upload a file to S3 compatible storage
    
    Returns True if successful, False otherwise
    """
    s3_endpoint = os.environ.get("S3_ENDPOINT")
    s3_bucket = os.environ.get("S3_BUCKET")
    s3_access_key = os.environ.get("S3_ACCESS_KEY")
    s3_secret_key = os.environ.get("S3_SECRET_KEY")
    s3_region = os.environ.get("S3_REGION", "us-east-1")
    
    if not all([s3_endpoint, s3_bucket, s3_access_key, s3_secret_key]):
        print("S3 configuration incomplete, skipping upload")
        return False
    
    try:
        s3_client = boto3.client(
            's3',
            endpoint_url=s3_endpoint,
            aws_access_key_id=s3_access_key,
            aws_secret_access_key=s3_secret_key,
            region_name=s3_region
        )
        
        print(f"Uploading {file_path} to S3 bucket {s3_bucket} with key {s3_key}")
        s3_client.upload_file(
            str(file_path),
            s3_bucket,
            s3_key
        )
        return True
    except ClientError as e:
        print(f"Error uploading to S3: {e}")
        return False


BACKUP_PROVIDERS: list[BackupProvider] = [
    BackupProvider(
        name="postgres",
        patterns=[
            "postgres",
            "tensorchord/pgvecto-rs",
            "nextcloud/aio-postgresql",
            "timescale/timescaledb",
        ],
        backup_method=backup_psql,
        file_extension="sql",
    ),
    BackupProvider(
        name="mysql",
        patterns=["mysql", "mariadb", "linuxserver/mariadb"],
        backup_method=backup_mysql,
        file_extension="sql",
    ),
    BackupProvider(
        name="redis",
        patterns=["redis"],
        backup_method=backup_redis,
        file_extension="rdb",
    ),
]


BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", "/var/backups"))
SCHEDULE = os.environ.get("SCHEDULE", "0 0 * * *")
SHOW_PROGRESS = sys.stdout.isatty()
COMPRESSION = os.environ.get("COMPRESSION", "plain")
INCLUDE_LOGS = bool(os.environ.get("INCLUDE_LOGS"))
TIMESTAMP = bool(os.environ.get("TIMESTAMP"))
TIMESTAMP_FORMAT = os.environ.get("TIMESTAMP_FORMAT", "%Y-%m-%d_%H-%M")
TIMESTAMP_ORDER = os.environ.get("TIMESTAMP_FORMAT", "after")
S3_ENABLED = bool(os.environ.get("S3_ENABLED"))
S3_PREFIX = os.environ.get("S3_PREFIX", "backups")
S3_KEEP_LOCAL = os.environ.get("S3_KEEP_LOCAL", "true").lower() == "true"


def get_backup_provider(container_names: Iterable[str]) -> Optional[BackupProvider]:
    for name in container_names:
        for provider in BACKUP_PROVIDERS:
            if any(fnmatch.fnmatch(name, pattern) for pattern in provider.patterns):
                return provider

    return None


def get_container_names(container: Container) -> Iterable[str]:
    names = set()
    for tag in container.image.tags:
        registry, image = docker.auth.resolve_repository_name(tag)

        # HACK: Strip "library" from official images
        if registry == docker.auth.INDEX_NAME:
            image = image.removeprefix("library/")

        image, tag_name = image.split(":", 1)
        names.add(image)
    return names


@pycron.cron(SCHEDULE)
def backup(now: datetime) -> None:
    print("Starting backup...")

    docker_client = docker.from_env()
    containers = docker_client.containers.list()

    backed_up_containers = []
    s3_uploaded_files = []

    print(f"Found {len(containers)} containers.")

    for container in containers:
        container_names = get_container_names(container)
        backup_provider = get_backup_provider(container_names)
        if backup_provider is None:
            continue

        if TIMESTAMP:
            timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
            if TIMESTAMP_ORDER.lower() == "before":
                filename = f"{timestamp}_{container.name}"
            else:
                filename = f"{container.name}_{timestamp}"
                
            backup_file = (
                BACKUP_DIR
                / f"{filename}.{backup_provider.file_extension}{get_compressed_file_extension(COMPRESSION)}")
        else:
            backup_file = (
                BACKUP_DIR
                / f"{container.name}.{backup_provider.file_extension}{get_compressed_file_extension(COMPRESSION)}")

        backup_temp_file_path = BACKUP_DIR / temp_backup_file_name()

        backup_command = backup_provider.backup_method(container)
        _, output = container.exec_run(backup_command, stream=True, demux=True)

        description = f"{container.name} ({backup_provider.name})"

        with open_file_compressed(
            backup_temp_file_path, COMPRESSION
        ) as backup_temp_file:
            with tqdm.wrapattr(
                backup_temp_file,
                method="write",
                desc=description,
                disable=not SHOW_PROGRESS,
            ) as f:
                for stdout, _ in output:
                    if stdout is None:
                        continue
                    f.write(stdout)

        os.replace(backup_temp_file_path, backup_file)

        if not SHOW_PROGRESS:
            print(description)

        backed_up_containers.append(container.name)
        
        if S3_ENABLED:
            s3_key = f"{S3_PREFIX}/{backup_file.name}"
            if upload_to_s3(backup_file, s3_key):
                s3_uploaded_files.append(s3_key)
                if not S3_KEEP_LOCAL:
                    backup_file.unlink()
                    print(f"Removed local file {backup_file} after S3 upload")

    duration = (datetime.now() - now).total_seconds()
    print(
        f"Backup of {len(backed_up_containers)} containers complete in {duration:.2f} seconds."
    )
    
    if S3_ENABLED:
        print(f"Uploaded {len(s3_uploaded_files)} files to S3.")

    if success_hook_url := get_success_hook_url():
        if INCLUDE_LOGS:
            log_data = "\n".join(backed_up_containers)
            if S3_ENABLED:
                log_data += "\n\nS3 Uploads:\n" + "\n".join(s3_uploaded_files)
            response = requests.post(success_hook_url, data=log_data)
        else:
            response = requests.get(success_hook_url)

        response.raise_for_status()


if __name__ == "__main__":
    if os.environ.get("SCHEDULE"):
        print(f"Running backup with schedule '{SCHEDULE}'.")
        pycron.start()
    else:
        backup(datetime.now())
