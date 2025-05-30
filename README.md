# docker-db-auto-backup (Forked)

![](https://github.com/GetParanoid/docker-db-auto-backup/workflows/CI/badge.svg)

A script to automatically back up all databases running under docker on a host, with optional compression and timestamp support.


> **This is a fork of [realorangeone/docker-db-auto-backup](https://github.com/realorangeone/docker-db-auto-backup).**
> This version includes additional functionality for timestamped file names and s3 compatible storage.

## Changes in this fork:
- Ability to add timestamps to the backup file name.
- New environment variables for timestamp configuration:
  - `TIMESTAMP=false` *(default: false)*
  - `TIMESTAMP_FORMAT=%Y-%m-%d_%H-%M` *(Defines the timestamp format using [Python's strftime](https://strftime.org/) syntax. )*
  - `TIMESTAMP_ORDER=after` *(determines whether the timestamp appears before or after the filename)*
- S3 compatible storage support for backup uploads
---

## Supported databases

- MySQL (including MariaDB and LSIO's MariaDB)
- PostgreSQL (including [TimescaleDB](https://www.timescale.com/), [pgvecto.rs](https://github.com/tensorchord/pgvecto.rs), and Nextcloud's [AIO](https://github.com/nextcloud/all-in-one))
- Redis

## Installation

This container requires access to the docker socket. This can be done either by mounting `/var/lib/docker.sock`, or using a HTTP proxy to provide it through `$DOCKER_HOST`.

Mount your backup directory as `/var/backups` (or override `$BACKUP_DIR`). Backups will be saved here based on the name of the container. Backups are not dated or compressed.

Backups run daily at midnight. To change this, add a cron-style schedule to `$SCHEDULE`. For more information on the format of the cron strings, please see the [croniter documentation on PyPI](https://pypi.org/project/croniter/).

### Success hooks

When backups are completed successfully, a request can be made to the URL defined in `$SUCCESS_HOOK_URL`. By default, a `GET` request is made. To include logs, also set `$INCLUDE_LOGS` to a non-empty value, which sends a `POST` request instead with helpful details in the body.

Note: Previous versions also supported `$HEALTHCHECKS_ID`, `$HEALTHCHECKS_HOST` and `$UPTIME_KUMA_URL`, or native support for [healthchecks.io](https://healthchecks.io) and [Uptime Kuma](https://github.com/louislam/uptime-kuma/) respectively. These are all still supported, however `$SUCCESS_HOOK_URL` is preferred.

### Compression

Files are backed up uncompressed by default, on the assumption a snapshotting or native compressed filesystem is being used (eg ZFS). To enable compression, set `$COMPRESSION` to one of the supported algorithms:

- `gzip`
- `lzma` / `xz`
- `bz2`
- `plain` (no compression - the default)

### S3 Compatible Storage

Backups can be automatically uploaded to any S3 compatible storage service. To enable this feature, set the following environment variables:

- `S3_ENABLED=true` - Enable S3 uploads (default: false)
- `S3_ENDPOINT` - S3 compatible endpoint URL (e.g., `https://s3.amazonaws.com`)
- `S3_BUCKET` - Bucket name to store backups
- `S3_ACCESS_KEY` - Access key for authentication
- `S3_SECRET_KEY` - Secret key for authentication
- `S3_REGION` - Region name (default: `us-east-1`)
- `S3_PREFIX` - Prefix for S3 object keys (default: `backups`)
- `S3_KEEP_LOCAL` - Whether to keep local copies after S3 upload (default: `true`)

When S3 uploads are enabled, backup files will be uploaded to the specified bucket with keys in the format `{S3_PREFIX}/{filename}`. If `S3_KEEP_LOCAL` is set to `false`, local backup files will be deleted after successful upload to save disk space.

### Example `docker-compose.yml`

```yml
version: "2.3"

services:
  backup:
    image: ghcr.io/GetParanoid/db-auto-backup:latest
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      - ./backups:/var/backups
    environment:
      - TZ=${TZ} # For accurate timestamping i.e America/Chicago
      - SCHEDULE=${SCHEDULE} #Standard CRON format (Default: 0 0 * *)
      - SUCCESS_HOOK_URL=${SUCCESS_HOOK_URL}
      - INCLUDE_LOGS=${INCLUDE_LOGS}
      - COMPRESSION=${COMPRESSION} # gzip / lzma / zx / bz2 / plain (disabled - default)
      - TIMESTAMP=${TIMESTAMP} # Enable or disable timestamps. True / False (Default)
      - TIMESTAMP_FORMAT=${TIMESTAMP_FORMAT} # Timestamp format Default: '%Y-%m-%d_%H-%M'
      - TIMESTAMP_ORDER=${TIMESTAMP_ORDER} # Write timestamp before / after (default) filename
      # S3 Configuration
      - S3_ENABLED=${S3_ENABLED} # Enable S3 uploads. True / False (Default)
      - S3_ENDPOINT=${S3_ENDPOINT} # S3 compatible endpoint URL
      - S3_BUCKET=${S3_BUCKET} # Bucket name
      - S3_ACCESS_KEY=${S3_ACCESS_KEY} # Access key
      - S3_SECRET_KEY=${S3_SECRET_KEY} # Secret key
      - S3_REGION=${S3_REGION} # Region name (Default: us-east-1)
      - S3_PREFIX=${S3_PREFIX} # Prefix for S3 object keys (Default: backups)
      - S3_KEEP_LOCAL=${S3_KEEP_LOCAL} # Keep local copies after S3 upload (Default: true)
```
> **An example .env file can be found here: [docker-db-auto-backup/blob/master/.env.example](https://github.com/GetParanoid/docker-db-auto-backup/blob/master/.env.example)**  


### Oneshot

You may want to use this container to run backups just once, rather than on a schedule. To achieve this, set `$SCHEDULE` to an empty string, and the backup will run just once. This may be useful in conjunction with an external scheduler.
