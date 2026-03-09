# ProjectFlow v4.0 — Production Deployment Guide
### For Railway · Render · Fly.io
> Written for non-developers. Every step is explained.

---

## What's in this package

| File | What it does |
|------|-------------|
| `app.py` | Your app — **not changed at all** |
| `requirements.txt` | Tells the server which Python libraries to install |
| `Dockerfile` | Recipe to package your app into a container |
| `start.sh` | Runs before your app starts; protects your data across redeploys |
| `gunicorn.conf.py` | Production web server settings (replaces the dev server) |
| `railway.toml` | Config file for Railway |
| `render.yaml` | Config file for Render |
| `fly.toml` | Config file for Fly.io |
| `.gitignore` | Tells Git not to upload your database or secrets |
| `.env.example` | Template for environment variables |

---

## ⚠️ Important: Why you need a "Persistent Volume"

Your app stores data in two places:
- **`projectflow.db`** — the database (users, projects, tasks, messages)
- **`pf_uploads/`** — files your team uploads to tasks

On PaaS platforms, every time you redeploy, the server resets to factory state. Without a persistent volume, **all your data is wiped on every deploy**.

The `start.sh` script solves this automatically — it links the volume into the right place before the app starts. You just need to create the volume (instructions below for each platform).

---

## Step 0 — Put your files on GitHub (required by all platforms)

All three platforms deploy from GitHub. Do this once:

1. Go to **github.com** → sign in (or create a free account)
2. Click **"New repository"** (green button, top right)
3. Name it `projectflow`, set it to **Private**, click **Create repository**
4. On the next page, click **"uploading an existing file"**
5. Drag and drop **all files from this package** into the upload area
6. Click **"Commit changes"**

Your code is now on GitHub. Pick a platform below.

---

## 🚂 Option A — Railway (Recommended for beginners)

**Cost:** ~$5/mo (Hobby plan) after a free trial  
**Easiest:** No credit card needed to try

### Step 1 — Create Railway account
1. Go to **railway.app** → click **"Start a New Project"**
2. Sign in with GitHub (click "Login with GitHub")

### Step 2 — Deploy from GitHub
1. Click **"Deploy from GitHub repo"**
2. Select your `projectflow` repository
3. Railway detects the `Dockerfile` automatically → click **"Deploy Now"**
4. Wait ~2 minutes for the build to finish (watch the logs)

### Step 3 — Add a Persistent Volume (CRITICAL)
Without this, your database resets on every redeploy.

1. In your Railway project, click your **projectflow service**
2. Click the **"Volumes"** tab
3. Click **"Add Volume"**
4. Set **Mount Path** to: `/data`
5. Set **Size** to: `1 GB`
6. Click **"Create"**
7. Railway will automatically redeploy — wait for it to finish

### Step 4 — Get your URL
1. Click your service → **"Settings"** tab
2. Under **"Domains"**, click **"Generate Domain"**
3. Railway gives you a free URL like `projectflow-abc123.up.railway.app`
4. Open that URL in your browser — ProjectFlow is live! 🎉

### Step 5 — Share with your team
- Share the Railway URL with teammates
- They can register using **"Create Account"** → **"Join Workspace"** with your invite code
- Find your invite code inside the app: **Settings ⚙ → Invite Code**

---

## 🎨 Option B — Render

**Cost:** Free tier available (app sleeps after 15 min idle); Starter plan $7/mo stays always-on  
**Persistent disk:** Requires at least the Starter plan ($7/mo) — free tier doesn't support it

### Step 1 — Create Render account
1. Go to **render.com** → click **"Get Started for Free"**
2. Sign in with GitHub

### Step 2 — Deploy via Blueprint (easiest)
1. In Render dashboard, click **"New +"** → **"Blueprint"**
2. Connect your `projectflow` GitHub repository
3. Render reads `render.yaml` automatically
4. Review the settings — the disk is already configured in the file
5. Click **"Apply"** → wait ~3 minutes for build

### Step 3 — Or deploy manually (if Blueprint doesn't work)
1. Click **"New +"** → **"Web Service"**
2. Connect your `projectflow` repository
3. Choose **Docker** as the runtime
4. Scroll down to **"Environment Variables"** → add:
   - `PORT` = `8080`
   - `DATA_DIR` = `/data`
5. Scroll to **"Disks"** → click **"Add Disk"**:
   - Name: `projectflow-data`
   - Mount Path: `/data`
   - Size: `1 GB`
6. Click **"Create Web Service"**

### Step 4 — Get your URL
Render shows the URL at the top of your service page, like:  
`https://projectflow.onrender.com`

---

## 🪰 Option C — Fly.io

**Cost:** ~$3-5/mo (generous free tier; volume costs ~$0.15/GB/mo)  
**Best for:** Lowest latency (choose a region near your team)

### Step 1 — Install the Fly CLI tool
On **Mac**: open Terminal, paste:
```
brew install flyctl
```
On **Windows**: open PowerShell as Administrator, paste:
```
iwr https://fly.io/install.ps1 -useb | iex
```

### Step 2 — Login
```
fly auth login
```
(opens browser, sign in or create account)

### Step 3 — Edit fly.toml
Open `fly.toml` in any text editor (Notepad works):
- Change `app = "projectflow"` to something unique like `app = "projectflow-yourname"`
- Change `primary_region = "sin"` to your nearest region:
  - `lax` = Los Angeles, `ord` = Chicago, `iad` = Virginia, `lhr` = London
  - `ams` = Amsterdam, `nrt` = Tokyo, `sin` = Singapore, `syd` = Sydney

### Step 4 — Create persistent volume
In Terminal/PowerShell, navigate to your project folder, then run:
```
fly volumes create projectflow_data --size 1 --region sin
```
(Replace `sin` with your chosen region)

### Step 5 — Deploy
```
fly deploy
```
Wait ~3 minutes. When done, it prints your URL like:
`https://projectflow-yourname.fly.dev`

---

## 🔑 After deployment — First time setup

1. Open your app URL in a browser
2. Log in with the demo account:  
   **Email:** `alice@dev.io` **Password:** `pass123`
3. Go to **Settings ⚙** (bottom of left sidebar)
4. Under **"Invite Code"**, copy your code and share it with teammates
5. Under **"AI Assistant"**, paste your Anthropic API key (get it from console.anthropic.com)
6. Optionally rename your workspace from "Demo Workspace" to your company name

---

## 🔁 How to update the app (future changes)

If you ever get an updated `app.py`:

1. Go to your GitHub repository
2. Click on `app.py` → click the pencil icon to edit → paste new content → commit
3. The platform automatically redeploys within 1-2 minutes
4. **Your data is safe** — it lives on the persistent volume, not in the code

---

## 🆘 Troubleshooting

**App won't start / build fails**  
→ Check the build logs in the platform dashboard. Look for red error lines. The most common cause is a typo in a filename.

**Database keeps resetting after redeploy**  
→ You didn't add the persistent volume, or mounted it at the wrong path. It must be `/data`.

**"pf_static" error / React won't load**  
→ The app downloads React on first boot. Check your internet connectivity from the platform. The static files are cached on the volume after the first download.

**AI assistant says "No API Key"**  
→ Go to Settings ⚙ inside the app → paste your Anthropic API key → Save.

**Fly.io: "app name already taken"**  
→ Change the `app = "projectflow"` line in `fly.toml` to something unique.

**Render: app sleeps and takes 30 seconds to wake**  
→ You're on the free tier. Upgrade to Starter ($7/mo) for always-on.

---

## 📊 Platform comparison

| | Railway | Render | Fly.io |
|---|---|---|---|
| **Easiest setup** | ✅ Best | ✅ Good | ⚠️ CLI required |
| **Free tier** | Trial only | ✅ (sleeps) | ✅ Generous |
| **Persistent disk** | ✅ Easy | ✅ Easy | ✅ Easy |
| **Always-on cost** | ~$5/mo | ~$7/mo | ~$3/mo |
| **Custom domain** | ✅ Free | ✅ Free | ✅ Free |
| **HTTPS** | Auto ✅ | Auto ✅ | Auto ✅ |
| **Best for** | Beginners | Teams | Cost-saving |
