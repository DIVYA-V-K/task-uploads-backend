import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import boto3
from botocore.exceptions import ClientError
import mysql.connector
from mysql.connector import Error

# --- Initialize Flask ---
app = Flask(__name__)
CORS(app)

# --- Configuration from Environment Variables ---
AWS_ACCESS_KEY = os.environ.get("AWS_ACCESS_KEY_ID")
AWS_SECRET_KEY = os.environ.get("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.environ.get("AWS_REGION", "ap-south-1")
BUCKET_NAME = os.environ.get("S3_BUCKET", "zinivd-task-uploads")

# MySQL Configuration (optional - only if you use remote MySQL)
MYSQL_HOST = os.environ.get("MYSQL_HOST", "localhost")
MYSQL_USER = os.environ.get("MYSQL_USER", "root")
MYSQL_PASSWORD = os.environ.get("MYSQL_PASSWORD", "")
MYSQL_DATABASE = os.environ.get("MYSQL_DATABASE", "file-uploads")

# Validate AWS credentials
if not (AWS_ACCESS_KEY and AWS_SECRET_KEY):
    raise RuntimeError("❌ Missing AWS credentials! Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY in Render environment variables.")

# --- Initialize S3 Client ---
s3 = boto3.client(
    "s3",
    aws_access_key_id=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    region_name=AWS_REGION
)

# --- Allowed File Extensions ---
ALLOWED_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.mp4', '.mov', '.avi', '.webm'}

def allowed_file(filename: str) -> bool:
    """Check if the file has an allowed extension."""
    return any(filename.lower().endswith(ext) for ext in ALLOWED_EXTENSIONS)

def create_presigned_url(key: str, method: str, expires_in: int = 300, content_type: str = None):
    """Generate a presigned URL for S3."""
    try:
        params = {"Bucket": BUCKET_NAME, "Key": key}
        if content_type:
            params["ContentType"] = content_type
        return s3.generate_presigned_url(ClientMethod=method, Params=params, ExpiresIn=expires_in)
    except ClientError as e:
        app.logger.error(f"Presigned URL error: {e}")
        return None

def get_db_connection():
    """Create MySQL connection (optional - comment out if not using MySQL)"""
    try:
        return mysql.connector.connect(
            host=MYSQL_HOST,
            user=MYSQL_USER,
            password=MYSQL_PASSWORD,
            database=MYSQL_DATABASE
        )
    except Error as e:
        app.logger.error(f"MySQL connection error: {e}")
        return None

# --- Routes ---

@app.route("/", methods=["GET"])
def home():
    """Health check endpoint"""
    return jsonify({
        "status": "✅ Backend is running!",
        "bucket": BUCKET_NAME,
        "region": AWS_REGION
    }), 200

@app.route("/generate-upload-url", methods=["POST"])
def generate_upload_url():
    """Generate presigned URL for uploading to S3"""
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

        return jsonify({"url": url, "filename": filename}), 200

    except Exception as e:
        app.logger.error(f"Upload URL error: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/files", methods=["GET"])
def list_files():
    """List all files in S3 bucket"""
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
        app.logger.error(f"List files error: {e}")
        return jsonify({"error": f"AWS Error: {str(e)}"}), 500

@app.route("/save-file-info", methods=["POST"])
def save_file_info():
    """Save file metadata to MySQL (optional)"""
    data = request.get_json(force=True)
    filename = data.get("filename")
    content_type = data.get("contentType", "application/octet-stream")

    if not filename:
        return jsonify({"error": "Filename is required"}), 400

    # Comment out MySQL logic if not using database
    try:
        conn = get_db_connection()
        if not conn:
            return jsonify({"message": "File uploaded (MySQL not configured)"}), 200
        
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
        return jsonify({"message": "File uploaded (MySQL error)", "error": str(e)}), 200

@app.route("/delete-file", methods=["DELETE"])
def delete_file():
    """Delete file from S3"""
    data = request.get_json(force=True)
    filename = data.get("filename")

    if not filename:
        return jsonify({"error": "Filename is required"}), 400

    try:
        s3.delete_object(Bucket=BUCKET_NAME, Key=filename)
        return jsonify({"message": f"🗑️ {filename} deleted successfully"}), 200
    except ClientError as e:
        app.logger.error(f"Delete error: {e}")
        return jsonify({"error": f"AWS Error: {str(e)}"}), 500

# --- Run Server ---
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
