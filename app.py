from flask import Flask, request, jsonify
import os
import subprocess

app = Flask(__name__)


@app.route("/monday-webhook", methods=["POST"])
def monday_webhook():
    """Receive a Monday.com-style webhook payload and trigger the update script."""
    print("=" * 60)
    print("Webhook received")
    print(request.json)

    env = os.environ.copy()
    script_path = os.path.join(os.path.dirname(__file__), "scripts", "update-langfuse.sh")

    result = subprocess.run(
        ["bash", script_path],
        capture_output=True,
        text=True,
        env=env,
    )

    print(result.stdout)

    if result.stderr:
        print(result.stderr)

    return jsonify({
        "status": "success" if result.returncode == 0 else "error",
        "output": result.stdout,
        "error": result.stderr,
    })


@app.route("/")
def home():
    return "Langfuse Webhook Running"


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
