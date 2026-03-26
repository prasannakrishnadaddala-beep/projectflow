
from flask import Flask, request, jsonify, session
import os

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "change-me")

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")

def admin_required(f):
    def wrapper(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    wrapper.__name__ = f.__name__
    return wrapper

@app.route("/api/admin/login", methods=["POST"])
def admin_login():
    data = request.json
    if data and data.get("password") == ADMIN_PASSWORD:
        session["admin_logged_in"] = True
        return jsonify({"success": True})
    return jsonify({"error": "Invalid credentials"}), 401

@app.route("/api/admin/logout", methods=["GET", "POST"])
def admin_logout():
    session.pop("admin_logged_in", None)
    return jsonify({"message": "Logged out"})

@app.route("/api/admin/workspaces", methods=["GET"])
@admin_required
def get_workspaces():
    return jsonify({"workspaces": []})

@app.route("/")
def home():
    return "Vewit Enterprise Backend Running"

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
