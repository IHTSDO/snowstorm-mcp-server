# Deployment and branch flow

Two long-lived branches, each tied to an environment:

| Branch    | Environment  | Deploys                                        |
|-----------|--------------|------------------------------------------------|
| `staging` | `staging`    | automatically on every push                    |
| `main`    | `production` | on every push, after a reviewer approves the job |

## Day-to-day flow

1. Branch from `staging`: `git switch -c feat/thing origin/staging`.
2. Open a PR into **`staging`**. CI must pass. Squash or merge, as you like.
3. Merging deploys to the staging host. Test there.
4. To release, open a PR from `staging` into **`main`** and merge it with a
   **merge commit** (the only method allowed on `main`). Squashing would give
   `main` commits that `staging` doesn't have, and every later promotion would
   conflict.
5. The production deploy job waits for approval under the run's
   *Review deployments* button. Approve it to ship.

PRs into `main` from any branch other than `staging` or `hotfix/*` fail the
`source-branch` check.

### Hotfixes

Branch `hotfix/<name>` from `main`, PR it into `main`, ship it, then merge
`main` back into `staging` so the fix isn't lost on the next promotion:

```bash
git switch staging && git pull && git merge origin/main && git push
```

## What a deploy does

`.github/workflows/deploy.yml` runs CI, builds the image and pushes it to
`ghcr.io/ihtsdo/snowstorm-mcp-server` tagged `sha-<commit>` and `<branch>`.
It then SSHes to the environment's host, passing only the image digest.
On the host, [`deploy/remote-deploy.sh`](../deploy/remote-deploy.sh):

1. pulls the image;
2. validates the host's `config.yaml` against the new code, so a schema
   mismatch fails here while the old container keeps serving;
3. recreates the container (same flags as before: `--memory`, loopback port,
   read-only config mount);
4. waits for the Docker healthcheck, and if it doesn't go healthy, **rolls
   back** to the previous image and fails the job;
5. tags `snowstorm-mcp-server:current` / `:previous` and prunes untagged
   images.

### Rolling back

Run the **Deploy** workflow manually (*Actions → Deploy → Run workflow*) from
the branch for the environment (`main` for production) and enter the full
commit SHA of the build to restore. Every build stays in GHCR, so any past
commit on that branch can be redeployed. On the host,
`snowstorm-mcp-server:previous` is also available.

## One-time host setup

Do this on each host (staging and production).

```bash
# 1. A deploy user that can run docker and nothing else useful
sudo useradd --system --create-home --shell /bin/bash deploy
sudo usermod -aG docker deploy

# 2. Install the script
sudo install -m 755 deploy/remote-deploy.sh /usr/local/bin/snowstorm-mcp-deploy

# 3. Host overrides, only if this host differs from the defaults at the top
#    of the script (container name, config path, port, memory).
sudoedit /etc/snowstorm-mcp-deploy.env

# 4. Make sure the deploy user can read the config
ls -l /opt/snowstorm-mcp-server/config.yaml
```

Generate a key pair **per environment** (on your machine, not the host):

```bash
ssh-keygen -t ed25519 -N '' -C gha-deploy-staging -f deploy_staging
```

Add the public key to `~deploy/.ssh/authorized_keys` on the host, **pinned
to the script** so it can't open a shell:

```
command="/usr/local/bin/snowstorm-mcp-deploy",restrict ssh-ed25519 AAAA... gha-deploy-staging
```

Get the host key for pinning, from a trusted network path:

```bash
ssh-keyscan -t ed25519 <host>
```

The security group must allow port 22 from GitHub Actions runners. The
deploy user can only run the script, and the script only accepts an image
reference from this repo's package.

### GitHub environment secrets

Set these in *Settings → Environments → staging / production*:

| Secret               | Value                                          |
|----------------------|------------------------------------------------|
| `DEPLOY_HOST`        | host name or IP                                |
| `DEPLOY_USER`        | `deploy`                                       |
| `DEPLOY_SSH_KEY`     | contents of the private key (`deploy_staging`) |
| `DEPLOY_KNOWN_HOSTS` | the `ssh-keyscan` output line                  |

Then delete the local private key.

### GHCR package visibility

The first build creates the `snowstorm-mcp-server` package under the org. To
let hosts pull without credentials, set it to **public** in the package
settings. If it must stay private, run `docker login ghcr.io` as the deploy
user with a read-only (`read:packages`) token.

### Migrating an existing host

The first automated deploy replaces the hand-built container of the same name,
and records the old image as the rollback target if the new one is unhealthy.
`config.yaml` is untouched, but back it up anyway; it isn't in git.
