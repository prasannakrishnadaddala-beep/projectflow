#!/bin/bash

echo "╔════════════════════════════════════════════════════════════╗"
echo "║     ProjectFlow - Railway Deployment (Clean Start)        ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""

# Check if git is installed
if ! command -v git &> /dev/null; then
    echo "❌ Git is not installed. Please install git first."
    exit 1
fi

echo "✅ Git found"

# Initialize git if needed
if [ ! -d ".git" ]; then
    echo "📦 Initializing git repository..."
    git init
    echo "✅ Git initialized"
fi

# Add files
echo "📁 Adding files..."
git add .

# Get commit message
read -p "Commit message (default: 'Initial commit'): " commit_msg
if [ -z "$commit_msg" ]; then
    commit_msg="Initial commit - ProjectFlow"
fi

# Commit
git commit -m "$commit_msg"
echo "✅ Files committed"

# Check for remote
if ! git remote | grep -q "origin"; then
    echo ""
    echo "🔗 GitLab remote not configured"
    read -p "Enter your GitLab repository URL: " gitlab_url
    
    if [ ! -z "$gitlab_url" ]; then
        git remote add origin "$gitlab_url"
        echo "✅ Remote added"
    else
        echo "⚠️  No remote URL provided. Add it later with:"
        echo "   git remote add origin YOUR_GITLAB_URL"
        echo ""
    fi
fi

# Push to GitLab
echo ""
echo "🚀 Ready to push to GitLab!"
read -p "Push now? (y/n): " push_now

if [ "$push_now" = "y" ] || [ "$push_now" = "Y" ]; then
    echo "📤 Pushing to GitLab..."
    git push -u origin main 2>&1
    
    if [ $? -eq 0 ]; then
        echo "✅ Successfully pushed to GitLab!"
    else
        echo "⚠️  Push failed. You may need to:"
        echo "   1. Set upstream: git push --set-upstream origin main"
        echo "   2. Or force push: git push -f origin main"
    fi
fi

echo ""
echo "╔════════════════════════════════════════════════════════════╗"
echo "║                    Next Steps                              ║"
echo "╚════════════════════════════════════════════════════════════╝"
echo ""
echo "1. Go to https://railway.app"
echo "2. Click 'New Project'"
echo "3. Select 'Deploy from GitLab repo'"
echo "4. Choose your projectflow repository"
echo "5. Wait ~2 minutes for deployment"
echo ""
echo "6. Add persistent volume:"
echo "   Settings → Volumes → + New Volume"
echo "   Mount Path: /data"
echo "   Size: 1 GB"
echo ""
echo "7. Your app will be live at:"
echo "   https://your-project.railway.app"
echo ""
echo "✅ Deployment ready! 🚀"
