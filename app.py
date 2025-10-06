
from dotenv import load_dotenv
load_dotenv()  # Loads variables from .env

import mysql.connector
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import boto3
from botocore.exceptions import ClientError
import os

# --- Initialize Flask ---
app = Flask(__name__)
CORS(app)

# --- Serve React frontend ---
@app.route("/", defaults={"path": ""})
@app.route("/<path:path>")
def serve_react(path):
    if path != "" and os.path.exists(f"frontend/build/{path}"):
        return send_from_directory("frontend/build", path)
    else:
        return send_from_directory("frontend/build", "index.html")


# --- Configuration ---


AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = "ap-south-1"
BUCKET_NAME = "zinivd-task-uploads"

if not (AWS_ACCESS_KEY and AWS_SECRET_KEY):
    raise RuntimeError("Missing AWS credentials! Set AWS_ACCESS_KEY and AWS_SECRET_KEY.")

# --- Initialize S3 Client ---
s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)

# --- Allowed Extensions ---
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.mp4', '.mov', '.avi', '.webm'}

def allowed_file(filename: str) -> bool:
    """Check if the file has an allowed extension."""
    return any(filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS)

def create_presigned_url(key: str, method: str, expires_in: int = 300, content_type: str = None):
    """Helper function to generate a presigned URL for S3."""
    try:
        params = {"Bucket": BUCKET_NAME, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        return s3.generate_presigned_url(ClientMethod=method, Params=params, ExpiresIn=expires_in)
    except ClientError as e:
        app.logger.error(f"Presigned URL error: {e}")
        return None

def get_db_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="",
        database="file-uploads"
    )

# --- Routes ---
@app.route("/generate-upload-url", methods=["POST"])
def generate_upload_url():
    try:
        data = request.get_json(force=True)
        filename = data.get("filename")
        content_type = data.get("contentType", "application/octet-stream")

        if not filename:
            return jsonify({"error": "Filename is required"}), 400
        if not allowed_file(filename):
            return jsonify({"error": "File type not allowed"}), 400

        url = create_presigned_url(filename, "put_object", expires_in=300, content_type=content_type)
        if not url:
            return jsonify({"error": "Failed to generate upload URL"}), 500

        # --- Store info in MySQL ---
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO uploads (filename, content_type) VALUES (%s, %s)",
                (filename, content_type)
            )
            conn.commit()
            cursor.close()
            conn.close()
        except Exception as e:
            app.logger.error(f"MySQL insert error: {e}")

        return jsonify({"url": url, "filename": filename}), 200

    except Exception as e:
        print("Error:", e)
        return jsonify({"error": str(e)}), 500

@app.route("/files", methods=["GET"])
def list_files():
    try:
        response = s3.list_objects_v2(Bucket=BUCKET_NAME)
        contents = response.get("Contents", [])

        file_list = [
            {
                "name": f["Key"],
                "url": create_presigned_url(f["Key"], "get_object", expires_in=7*24*3600),
                "size": f["Size"],
                "lastModified": f["LastModified"].isoformat(),
            }
            for f in contents
            if create_presigned_url(f["Key"], "get_object")
        ]

        return jsonify({"files": file_list}), 200
    except ClientError as e:
        return jsonify({"error": f"AWS Error: {e}"}), 500


@app.route("/save-file-info", methods=["POST"])
def save_file_info():
    data = request.get_json(force=True)
    filename = data.get("filename")
    content_type = data.get("contentType", "application/octet-stream")

    if not filename:
        return jsonify({"error": "Filename is required"}), 400

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO uploads (filename, content_type) VALUES (%s, %s)",
            (filename, content_type)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"message": f'File "{filename}" info saved successfully'}), 200
    except Exception as e:
        app.logger.error(f"MySQL insert error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/delete-file", methods=["DELETE"])
def delete_file():
    data = request.get_json(force=True)
    filename = data.get("filename")

    if not filename:
        return jsonify({"error": "Filename is required"}), 400

    try:
        s3.delete_object(Bucket=BUCKET_NAME, Key=filename)
        return jsonify({"message": f"🗑️ {filename} deleted successfully"}), 200
    except ClientError as e:
        return jsonify({"error": f"AWS Error: {e}"}), 500

# --- Run Server ---
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=True)


