# Deploy to a single GCP VM (dev / demo only)

PostgreSQL runs in Docker; the backend runs directly on the VM with uv, as two systemd services (`zid@cell`, `zid@global`).

> **Not production.** There's no TLS, no authentication, a single VM and a single database. Production follows the cell topology in ADR-0011/ADR-0018 (AWS for Stage A; running production on GCP would need that ADR revisited). Restrict the firewall to your own IP.

## 1. On your laptop: create the VM and copy the code

Install the [gcloud CLI](https://cloud.google.com/sdk/docs/install), then run `gcloud auth login` and `gcloud config set project <PROJECT_ID>`.

```bash
gcloud compute instances create zoiko-id-dev \
  --zone=europe-west2-a --machine-type=e2-standard-2 \
  --image-family=debian-12 --image-project=debian-cloud \
  --boot-disk-size=30GB --tags=zoiko-id

# allow ONLY your IP to reach the API ports (find it with: curl ifconfig.me)
gcloud compute firewall-rules create zoiko-id-api --network=default \
  --allow=tcp:8000,tcp:8100 --target-tags=zoiko-id --source-ranges=<YOUR_IP>/32

# package the committed code and copy it to the VM
git archive --format=tar.gz -o zoiko_ids.tar.gz HEAD
gcloud compute scp zoiko_ids.tar.gz zoiko-id-dev:~ --zone=europe-west2-a
gcloud compute ssh zoiko-id-dev --zone=europe-west2-a
```

## 2. On the VM: install Docker and uv

```bash
sudo apt-get update && sudo apt-get install -y ca-certificates curl openssl
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER" && newgrp docker
curl -LsSf https://astral.sh/uv/0.13.0/install.sh | sh && . "$HOME/.local/bin/env"
```

## 3. On the VM: unpack, set a real DB password, start the database

```bash
mkdir -p ~/zoiko_ids && tar -xzf ~/zoiko_ids.tar.gz -C ~/zoiko_ids && cd ~/zoiko_ids
echo "ZID_DB_PASSWORD=$(openssl rand -hex 24)" > .env && chmod 600 .env
docker compose up -d postgres        # Postgres on 127.0.0.1:5432 only, schemas bootstrapped
docker compose ps postgres           # wait for "(healthy)"
```

## 4. On the VM: install and start the backend

```bash
cd ~/zoiko_ids/backend
uv sync --frozen --no-dev --package zoiko-id-platform   # exact versions from uv.lock; Python 3.13 via uv

sudo sed "s|__USER__|$USER|g; s|__HOME__|$HOME|g" ~/zoiko_ids/deploy/vm/zid@.service \
  | sudo tee /etc/systemd/system/zid@.service >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable --now zid@cell zid@global
```

## 5. Check it

```bash
systemctl status zid@cell zid@global --no-pager
curl -s localhost:8000/health/ready     # on the VM: "status":"ready"
journalctl -u zid@cell -f               # live JSON logs
```

From your laptop: `http://<VM_EXTERNAL_IP>:8000/docs`. Get the IP with `gcloud compute instances describe zoiko-id-dev --zone=europe-west2-a --format='get(networkInterfaces[0].accessConfigs[0].natIP)'`.

## Update to a new version

```bash
# laptop:  git archive --format=tar.gz -o zoiko_ids.tar.gz HEAD && gcloud compute scp zoiko_ids.tar.gz zoiko-id-dev:~ --zone=europe-west2-a
# VM:
tar -xzf ~/zoiko_ids.tar.gz -C ~/zoiko_ids          # keeps .env and the database volume
cd ~/zoiko_ids/backend && uv sync --frozen --no-dev --package zoiko-id-platform
sudo systemctl restart zid@cell zid@global
```

## Stop / remove

```bash
sudo systemctl disable --now zid@cell zid@global
cd ~/zoiko_ids && docker compose down          # add -v to delete the database
# laptop: gcloud compute instances delete zoiko-id-dev --zone=europe-west2-a
#         gcloud compute firewall-rules delete zoiko-id-api
```
