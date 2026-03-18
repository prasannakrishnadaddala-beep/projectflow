# ✅ ProjectFlow - Railway Deployment Checklist

## 📦 Files You Have

```
✅ app.py              - Your Flask application
✅ requirements.txt    - Python dependencies (Flask, gunicorn)
✅ Procfile           - Railway start command
✅ .gitignore         - Git excludes
✅ README.md          - Full documentation
✅ deploy.sh          - Deployment helper script
```

**This is EVERYTHING you need!** No other files required.

---

## 🚀 Deployment Steps

### [ ] Step 1: Push to GitLab

**Option A: Use the script (easiest)**
```bash
./deploy.sh
```

**Option B: Manual commands**
```bash
git init
git add .
git commit -m "Initial commit - ProjectFlow"
git remote add origin https://gitlab.com/YOUR-USERNAME/projectflow.git
git push -u origin main
```

---

### [ ] Step 2: Deploy on Railway

1. [ ] Go to https://railway.app
2. [ ] Click "New Project"
3. [ ] Select "Deploy from GitLab repo"
4. [ ] Choose your projectflow repository
5. [ ] Wait ~2 minutes for build
6. [ ] Build should succeed ✅

**Expected build output:**
```
Using Nixpacks
setup      │ python311
install    │ pip install -r requirements.txt
start      │ gunicorn --bind 0.0.0.0:$PORT app:app
✓ Build successful
✓ Deployment live
```

---

### [ ] Step 3: Add Persistent Storage

1. [ ] Railway Dashboard → Your Project
2. [ ] Settings → Volumes
3. [ ] Click "+ New Volume"
4. [ ] **Mount Path:** `/data`
5. [ ] **Size:** 1 GB
6. [ ] Click "Add"
7. [ ] Click "Redeploy"

**Why?** This ensures your database persists across deployments!

---

### [ ] Step 4: Test Your App

**Health Check:**
```bash
curl https://your-app.railway.app/health
```

**Expected response:**
```json
{"status": "healthy", "service": "ProjectFlow", "version": "4.0"}
```

**Access App:**
Visit: `https://your-app.railway.app/api/app`

Should show ProjectFlow login page ✅

**Test Login:**
- Email: `alice@dev.io`
- Password: `pass123`

---

### [ ] Step 5: Configure Email (Optional)

Railway Dashboard → Variables → Add:

```
EMAIL_ENABLED=true
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USERNAME=your-email@gmail.com
SMTP_PASSWORD=your-gmail-app-password
FROM_EMAIL=notifications@yourcompany.com
```

**Get Gmail App Password:**
1. Enable 2FA on Google
2. Visit: https://myaccount.google.com/apppasswords
3. Create password
4. Copy 16-character code
5. Paste in SMTP_PASSWORD

---

## ✅ Verification Checklist

After deployment, verify:

- [ ] Build succeeded (no errors in Railway logs)
- [ ] App accessible at Railway URL
- [ ] Health endpoint returns OK
- [ ] Login works
- [ ] Can create workspace
- [ ] Can create project
- [ ] Can create task
- [ ] Database persists (volume configured)
- [ ] Email notifications work (if configured)

---

## 🎯 Common Issues & Quick Fixes

### ❌ Build fails with "pip" error
**Fix:** Delete ALL config files except:
- app.py
- requirements.txt
- Procfile
- .gitignore

### ❌ App shows 502 error
**Fix:** 
1. Check Railway logs for errors
2. Ensure volume at `/data` is configured
3. Verify Procfile uses `$PORT`

### ❌ Database resets on deploy
**Fix:** Add persistent volume at `/data`

### ❌ Email not working
**Fix:**
1. Use Gmail App Password (not regular password)
2. Verify environment variables
3. Check Railway logs

---

## 📊 Expected File Sizes

```
app.py            ~150 KB    ✅
requirements.txt  ~50 bytes  ✅
Procfile         ~80 bytes  ✅
.gitignore       ~300 bytes ✅
```

Total repository size: ~200 KB

---

## 💰 Cost Estimate

**Free Tier:**
- $5 credit/month
- ~500 hours runtime
- Perfect for teams up to 50 users

**Your usage:**
- Small team (5-10 users): **FREE** ✅
- Medium team (50 users): ~$5-10/month
- Large team (500 users): ~$20-50/month

---

## 🔄 Auto-Deploy

Every time you push to GitLab, Railway auto-deploys!

```bash
# Make changes
git add .
git commit -m "Update feature"
git push origin main

# Railway automatically:
# 1. Detects push
# 2. Builds app
# 3. Deploys
# 4. Serves traffic
```

---

## 📞 Need Help?

**Railway Support:**
- Docs: https://docs.railway.app
- Discord: https://discord.gg/railway

**View Logs:**
```bash
railway logs  # if Railway CLI installed
```
Or: Railway Dashboard → Deployments → Logs

**Common Commands:**
```bash
# Install Railway CLI
npm install -g @railway/cli

# Login
railway login

# View logs
railway logs

# Open dashboard
railway open
```

---

## 🎉 Success Criteria

Your deployment is successful when:

✅ Build completes without errors  
✅ Health endpoint returns 200 OK  
✅ App login page loads  
✅ Can create workspace  
✅ Database persists after redeploy  
✅ No errors in Railway logs  

---

## 🚀 You're Ready!

Follow the checklist above and you'll have ProjectFlow running on Railway in **~5 minutes**!

**Next:** Invite your team and start managing projects! 🎊
