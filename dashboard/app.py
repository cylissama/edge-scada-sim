import os

from flask import Flask, jsonify, render_template

import bridge

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html", topic=bridge.TOPIC, group=bridge.GROUP_ID)


@app.route("/api/readings")
def readings():
    return jsonify(bridge.snapshot())


if __name__ == "__main__":
    bridge.start()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")), threaded=True)
