import boto3
import json
from PIL import Image, ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

class S3Service:
    def __init__(self):
        self.s3_client = boto3.client('s3')

    async def fetch_legend_files(self, bucket_name: str, user_id: str, submission_key: str) -> dict:
        """Fetch all legend files for a submission"""
        try:
            prefix = f"documents/{user_id}/{submission_key}/legend/"
            legend_files = self.s3_client.list_objects_v2(
                Bucket=bucket_name,
                Prefix=prefix
            )

            legend_data = {}
            for obj in legend_files.get('Contents', []):
                if obj['Key'].endswith('.json'):
                    response = self.s3_client.get_object(
                        Bucket=bucket_name,
                        Key=obj['Key']
                    )
                    body = response['Body'].read()
                    page_data = json.loads(body)
                    # Extract page number from filename (e.g., page1.json -> 1)
                    page_num = int(obj['Key'].split('/')[-1].replace('page', '').replace('.json', ''))
                    legend_data[str(page_num)] = page_data

            return legend_data

        except Exception as e:
            print(f"Error fetching legend files: {e}")
            raise Exception(f"Failed to fetch legend files: {str(e)}")

    def _get_json(self, bucket, key):
        response = self.s3_client.get_object(Bucket=bucket, Key=key)
        return json.loads(response['Body'].read())

    def _get_image(self, bucket, key):
        resp = self.s3_client.get_object(Bucket=bucket, Key=key)

        # Read the body in chunks to avoid partial reads
        body = resp["Body"]
        chunks = []
        for chunk in iter(lambda: body.read(1024 * 1024), b""):  # 1 MB chunks
            chunks.append(chunk)
        data = b"".join(chunks)

        # Optional: verify we got everything the server advertised
        content_len = resp.get("ContentLength")
        if content_len is not None and len(data) != content_len:
            # Reattempt once if sizes mismatch (network hiccup)
            body.close()
            resp = self.s3_client.get_object(Bucket=bucket, Key=key)
            data = resp["Body"].read()

        # Open robustly and fully decode now (so later crops don't trigger lazy decode errors)
        from io import BytesIO
        bio = BytesIO(data)
        img = Image.open(bio)

        # Force actual decode now
        img.load()

        # Free the raw byte buffers now that image is decoded
        bio.close()
        del bio, data

        # Convert to a reliable mode for OpenCV/NumPy pipelines
        if img.mode not in ("RGB", "RGBA"):
            img = img.convert("RGB")

        return img

    def generate_presigned_url(self, bucket_name: str, s3_key: str, expiration: int = 3600) -> str:
        """Generate a presigned URL for S3 object access

        Args:
            bucket_name: S3 bucket name
            s3_key: S3 object key
            expiration: URL expiration time in seconds (default 1 hour)

        Returns:
            Presigned URL string
        """
        try:
            # Generate presigned URL with explicit method and additional parameters
            response = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': s3_key,
                    'ResponseContentType': 'image/webp'  # Explicit content type for images
                },
                ExpiresIn=expiration,
                HttpMethod='GET'
            )
            print(f"[DEBUG] Presigned URL method: GET, expires in {expiration}s")
            return response
        except Exception as e:
            print(f"Error generating presigned URL for {s3_key}: {e}")
            raise Exception(f"Failed to generate presigned URL: {str(e)}")
