# Student Task Manager — Flask on AWS EC2 (IaaS)

Create, view, edit, delete, search and filter tasks (title, description, due date, priority, status) with a summary of total / completed / pending / overdue. Responsive web UI.

## Architecture (planned, not yet deployed)

```
Browser --HTTP :80--> Security Group --> EC2 (Amazon Linux 2023)
                                          Nginx :80 -> Gunicorn 127.0.0.1:8000 -> Flask
                                          SQLite file on the instance's EBS volume (/var/lib/taskmanager/tasks.db)
                                          cron -> backup_to_s3.sh --(IAM instance role, PutObject only)--> private S3 bucket
```

- **Service model: IaaS.** We manage OS, Nginx, Gunicorn, app and firewall; AWS manages the data centre and hypervisor.
- **Database:** SQLite on EBS. This is *not* Amazon RDS: a single-server file database with no automatic failover.
- **Security:** security group (HTTP open, SSH from your IP only), least-privilege IAM role (`s3:PutObject` on `backups/*` only), private S3 bucket with Block Public Access and encryption, no keys in code, CSRF tokens on all forms, optional login (HTTP Basic).
- **Known limits (be honest in the viva):** HTTP only, so the login password is not encrypted in transit; single instance, no auto scaling; SQLite handles one writer at a time.

## Status

| Item | Status |
|---|---|
| Local app and automated tests | Run them yourself (below) |
| EC2, S3, IAM, security group, backups | **Not deployed yet.** Create permissions are unverified |

## Project layout

```
student-task-manager/
├── app.py            ├── requirements.txt   ├── user_data.sh
├── templates/        ├── tests/test_app.py  ├── backup_to_s3.sh
│   └── index.html    ├── static/style.css   ├── README.md  └── .gitignore
```

## 1. Run locally (no AWS needed) — Windows PowerShell

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1        # if blocked: Set-ExecutionPolicy -Scope Process Bypass
pip install -r requirements.txt pytest
python app.py                     # open http://127.0.0.1:5000
```

Optional environment variables: `DB_PATH` (default `./tasks.db`), `SECRET_KEY`, `APP_USER` / `APP_PASSWORD` (set a password to require login; blank = no login), `PORT`.

## 2. Run the tests

```powershell
python -m pytest -v
```

Covers: health endpoint, create/view/edit/delete, validation, complete/incomplete, search and filters, summary and overdue counts, HTML escaping, persistence across app restarts, CSRF and login checks.

## 3. Before deploying: permissions and cost

**Check permissions (creates nothing):** open EC2 → *Launch instance*, S3 → *Create bucket* and IAM → *Roles → Create role*. Opening the pages does not prove you can create resources; a failure appears only on the final create step. If you get an **explicit deny / service control policy** error, stop. Do not try to work around it; see Troubleshooting.

**Possible charges** (not guaranteed to be covered by your Free plan or credits): EC2 instance hours, the EBS volume, the public IPv4 address, S3 storage/requests and outbound data transfer. Check *Billing → Free Tier* and *Credits* for what is covered. Pick an instance type the launch wizard marks **Free tier eligible**. A stopped instance still pays for its EBS volume. Do the cleanup (section 7) right after your demo.

Use a region your account allows, and the **same region** for the instance and the bucket.

## 4. Manual AWS deployment (console)

1. **GitHub:** push this folder to a public repo (`.gitignore` already excludes secrets and databases).
2. **S3 bucket:** *Create bucket* → globally unique name → **Block all public access: ON** → default encryption **SSE-S3** → create.
3. **IAM policy:** IAM → Policies → Create policy → JSON (replace `YOUR-BUCKET`), name `TaskManagerBackupPut`:
   ```json
   {"Version":"2012-10-17","Statement":[{"Effect":"Allow","Action":"s3:PutObject","Resource":"arn:aws:s3:::YOUR-BUCKET/backups/*"}]}
   ```
4. **IAM role:** Roles → Create role → trusted entity **AWS service → EC2** → attach only `TaskManagerBackupPut` → name `TaskManagerEC2Role`. No AdministratorAccess.
5. **Security group** (EC2 → Security Groups → Create): inbound **HTTP 80 from 0.0.0.0/0**; inbound **SSH 22 from My IP**. Nothing else.
6. **Edit `user_data.sh`:** set `REPO_URL`, `BACKUP_BUCKET` (and optionally `APP_PASSWORD`, letters/digits only).
7. **Launch instance:** Amazon Linux 2023 AMI, Free-tier-eligible type, key pair (keep the `.pem` out of the repo), the security group above, 8 GiB gp3 storage, *Advanced details → IAM instance profile* = `TaskManagerEC2Role`, *Metadata version* = V2 only, paste `user_data.sh` into *User data*.
8. Wait 2–3 minutes, then open `http://<public-IPv4>/` and `http://<public-IPv4>/health` (expect `{"status":"ok"}`). Use `http://`, not `https://`.
9. **Login:** user `student`; if you left `APP_PASSWORD` blank, read it via SSH: `sudo grep APP_PASSWORD /etc/taskmanager.env`.

## 5. Verify on AWS (only after deployment)

- **Persistence:** add a task → `sudo systemctl restart taskmanager` → refresh (task remains) → `sudo reboot`, wait, refresh (task still remains). Data file: `ls -l /var/lib/taskmanager/`.
- **S3 backup:** `sudo -u taskapp /opt/taskmanager/backup_to_s3.sh` → prints `Backup uploaded: s3://...` → confirm the object under `backups/` in the S3 console. Scheduled runs: `sudo journalctl -t taskmanager-backup`.
- **Services:** `sudo systemctl status taskmanager nginx`.
- **Logs:** `sudo journalctl -u taskmanager -n 50`, `/var/log/nginx/access.log`. CloudWatch is not set up; it would need the CloudWatch agent and extra IAM permissions, so only add it if your account allows it.

## 6. Troubleshooting

- **AccessDenied / "explicit deny in a service control policy":** an organisation-level rule blocks that action. Do not bypass it. Note the exact error, the action and the step, tell your faculty or account administrator, and demo the local version meanwhile.
- **Page does not open:** instance running? Public IP changes after stop/start. Security group has inbound 80? Using `http://`? First boot logs: `sudo tail -n 50 /var/log/cloud-init-output.log`.
- **502 Bad Gateway:** Gunicorn is down: `sudo systemctl status taskmanager`, `sudo journalctl -u taskmanager -n 50`; check `/etc/taskmanager.env` exists and `ls -ld /var/lib/taskmanager` is owned by `taskapp`.
- **user_data stopped early:** it refuses to run with the placeholder `REPO_URL`/`BACKUP_BUCKET`; the repo must be public.
- **SSH times out:** your IP changed; update the SSH rule to *My IP*.
- **S3 `AccessDenied` / `Unable to locate credentials`:** role not attached (EC2 → Actions → Security → Modify IAM role); policy resource must match `arn:aws:s3:::BUCKET/backups/*`; fix typos in `BACKUP_BUCKET` in `/etc/taskmanager.env`; bucket must be in your account.
- **Login keeps prompting:** user is `student`; password is in `/etc/taskmanager.env`.

## 7. Cleanup (avoid charges)

1. EC2 → **Terminate** the instance; confirm its volume is deleted (EC2 → Volumes).
2. S3 → **Empty**, then **Delete** the bucket.
3. IAM → delete role `TaskManagerEC2Role` and policy `TaskManagerBackupPut`.
4. EC2 → delete the security group and the key pair; release any Elastic IP (none is created here).
5. Next day, check *Billing* for unexpected charges.
