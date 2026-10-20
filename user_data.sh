#!/bin/bash
# EC2 user data for Amazon Linux 2023. Runs once, as root, at first boot.
# Edit the three values below BEFORE launching. No AWS keys belong here:
# the instance's IAM role supplies S3 credentials automatically.
set -euxo pipefail

REPO_URL="https://github.com/22-Arpita/student-task-manager.git"  # public repo with these files
BACKUP_BUCKET="YOUR-UNIQUE-BUCKET-NAME"                           # private S3 bucket (name only)
APP_PASSWORD=""   # letters/digits only. Blank = a random one is generated and saved on the server

APP_DIR=/opt/taskmanager
DATA_DIR=/var/lib/taskmanager
ENV_FILE=/etc/taskmanager.env

case "$REPO_URL$BACKUP_BUCKET" in
  *YOUR_USER*|*YOUR-UNIQUE*) echo "Edit REPO_URL and BACKUP_BUCKET first." >&2; exit 1;;
esac

dnf install -y python3-pip nginx git cronie
id taskapp >/dev/null 2>&1 || useradd --system --home-dir "$APP_DIR" --shell /sbin/nologin taskapp
[ -d "$APP_DIR/.git" ] || git clone "$REPO_URL" "$APP_DIR"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -r "$APP_DIR/requirements.txt"
chmod +x "$APP_DIR/backup_to_s3.sh"

# Region from instance metadata (IMDSv2)
TOKEN=$(curl -s -X PUT http://169.254.169.254/latest/api/token -H "X-aws-ec2-metadata-token-ttl-seconds: 300")
REGION=$(curl -s -H "X-aws-ec2-metadata-token: $TOKEN" http://169.254.169.254/latest/meta-data/placement/region)
[ -n "$APP_PASSWORD" ] || APP_PASSWORD=$(python3 -c "import secrets;print(secrets.token_hex(8))")

# Persistent data lives on the EBS root volume, outside the code directory
mkdir -p "$DATA_DIR"
chown taskapp:taskapp "$DATA_DIR"
chmod 750 "$DATA_DIR"

cat > "$ENV_FILE" <<EOF
SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_hex(32))")
DB_PATH=$DATA_DIR/tasks.db
APP_USER=student
APP_PASSWORD=$APP_PASSWORD
BACKUP_BUCKET=$BACKUP_BUCKET
AWS_DEFAULT_REGION=$REGION
EOF
chown root:taskapp "$ENV_FILE"
chmod 640 "$ENV_FILE"

cat > /etc/systemd/system/taskmanager.service <<'UNIT'
[Unit]
Description=Student Task Manager (Gunicorn)
After=network.target

[Service]
User=taskapp
Group=taskapp
WorkingDirectory=/opt/taskmanager
EnvironmentFile=/etc/taskmanager.env
ExecStart=/opt/taskmanager/venv/bin/gunicorn --workers 2 --bind 127.0.0.1:8000 "app:create_app()"
Restart=always

[Install]
WantedBy=multi-user.target
UNIT

# Minimal Nginx config replacing the stock one (reverse proxy to Gunicorn)
[ -f /etc/nginx/nginx.conf.orig ] || cp /etc/nginx/nginx.conf /etc/nginx/nginx.conf.orig
cat > /etc/nginx/nginx.conf <<'NGINX'
user nginx;
worker_processes auto;
error_log /var/log/nginx/error.log;
pid /run/nginx.pid;
events { worker_connections 1024; }
http {
    include /etc/nginx/mime.types;
    default_type application/octet-stream;
    access_log /var/log/nginx/access.log;
    server_tokens off;
    client_max_body_size 1m;
    server {
        listen 80 default_server;
        server_name _;
        add_header X-Content-Type-Options nosniff always;
        add_header X-Frame-Options DENY always;
        location / {
            proxy_pass http://127.0.0.1:8000;
            proxy_set_header Host $host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        }
    }
}
NGINX
nginx -t

# Daily backup at 02:00 server time (UTC); output goes to the system journal
echo '0 2 * * * taskapp /opt/taskmanager/backup_to_s3.sh 2>&1 | logger -t taskmanager-backup' > /etc/cron.d/taskmanager-backup

systemctl daemon-reload
systemctl enable --now taskmanager nginx crond
