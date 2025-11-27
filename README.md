# Reviewers Executive Summary Generator Service

A standalone Docker service for generating reviewer's executive summaries for Traffic Control Plan (TCP) drawings using OpenAI's GPT-4o model.

## Features

- FastAPI-based REST API
- Integration with AWS S3 for drawing and legend data
- OpenAI Assistant API for multimodal analysis
- Automatic thread and config management
- JSON-formatted executive summaries

## Prerequisites

- Docker and Docker Compose installed
- AWS credentials with S3 access
- OpenAI API key
- Python 3.11+ (for local development)

## Project Structure

```
reviewers-exec-summary/
├── main.py                          # FastAPI application entry point
├── services/
│   ├── __init__.py
│   ├── reviewers_exec_summary.py   # Core summary generation logic
│   └── s3_service.py                # S3 integration
├── requirements.txt                 # Python dependencies
├── Dockerfile                       # Docker container definition
├── docker-compose.yml               # Docker Compose configuration
├── .env                             # Environment variables
└── README.md                        # This file
```

## Setup

### 1. Environment Variables

Update the `.env` file with your credentials:

```env
# AWS Configuration
AWS_DEFAULT_REGION=ap-southeast-1
AWS_ACCESS_KEY_ID=your_access_key
AWS_SECRET_ACCESS_KEY=your_secret_key
AWS_S3_BUCKET=your_bucket_name

# OpenAI Configuration
OPENAI_API_KEY=your_openai_api_key
```

### 2. Build and Run with Docker Compose

```bash
# Build and start the service
docker-compose up -d

# View logs
docker-compose logs -f

# Stop the service
docker-compose down
```

### 3. Alternative: Run Locally (Development)

```bash
# Install dependencies
pip install -r requirements.txt

# Run the service
python main.py
```

## API Endpoints

### Generate Executive Summary

**POST** `/generate-exec-summary`

Generate a reviewer's executive summary for a TCP drawing.

**Request Body:**
```json
{
  "drawing_s3_key": "documents/user123/submission1/rasterize/page1.png",
  "submission_key": "submission2",
  "page_num": "1",
  "old_submission_key": "submission1",
  "user_id": "user123",
  "new_drawing_s3_key": "documents/user123/submission2/rasterize/page1.png",
  "legend_data": []
}
```

**Parameters:**
- `drawing_s3_key`: S3 key for the old submission's drawing image
- `submission_key`: Current submission key (where results will be saved)
- `page_num`: Page number (numeric string, e.g., "1", "2", etc.)
- `old_submission_key`: Old submission key for comparison (used in reconciliation path)
- `user_id`: (Optional) User ID - will be extracted from drawing_s3_key if not provided
- `new_drawing_s3_key`: (Optional) S3 key for the new submission's drawing image
- `legend_data`: (Optional) Legend detection data - will be fetched from S3 if not provided

**Output Path:**
Results are saved to: `documents/{user_id}/{submission_key}/reconciliation/{old_submission_key}/{page_num}/reviewers_exec_summary.json`

**Response:**
```json
{
  "status": "success",
  "data": {
    "report_text": "{\n  \"title\": \"...\",\n  \"purpose_and_setting\": \"...\",\n  ...\n}",
    "assistant_id": "asst_...",
    "thread_id": "thread_..."
  }
}
```

### Health Check

**GET** `/health`

Check if the service is running.

**Response:**
```json
{
  "status": "healthy",
  "service": "reviewers-exec-summary"
}
```

## Usage Examples

### cURL

```bash
curl -X POST http://localhost:8001/generate-exec-summary \
  -H "Content-Type: application/json" \
  -d '{
    "drawing_s3_key": "documents/user123/submission1/rasterize/page1.png",
    "submission_key": "submission2",
    "page_num": "1",
    "old_submission_key": "submission1",
    "user_id": "user123",
    "new_drawing_s3_key": "documents/user123/submission2/rasterize/page1.png"
  }'
```

### Python

```python
import requests

response = requests.post(
    "http://localhost:8001/generate-exec-summary",
    json={
        "drawing_s3_key": "documents/user123/submission1/rasterize/page1.png",
        "submission_key": "submission2",
        "page_num": "1",
        "old_submission_key": "submission1",
        "user_id": "user123",
        "new_drawing_s3_key": "documents/user123/submission2/rasterize/page1.png"
    }
)

print(response.json())
```

### From Main Comment Reconciliation Service

```python
import httpx

async def get_exec_summary(drawing_s3_key, submission_key, page_num, old_submission_key, user_id, new_drawing_s3_key):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "http://reviewers-exec-summary-service:8001/generate-exec-summary",
            json={
                "drawing_s3_key": drawing_s3_key,
                "submission_key": submission_key,
                "page_num": page_num,
                "old_submission_key": old_submission_key,
                "user_id": user_id,
                "new_drawing_s3_key": new_drawing_s3_key
            }
        )
        return response.json()
```

## Docker Configuration

### Port

- Service runs on port **8001**
- Exposed to host on port **8001**

### Network

- Uses bridge network `app-network`
- Can be connected to other services using the same network

### Health Check

- Endpoint: `/health`
- Interval: 30 seconds
- Timeout: 10 seconds
- Retries: 3

## Monitoring

### View Container Status

```bash
docker ps | grep reviewers-exec-summary
```

### View Logs

```bash
docker-compose logs -f reviewers-exec-summary
```

### Check Health

```bash
curl http://localhost:8001/health
```

## Integration with Main Service

To integrate with the main comment-reconciliation service, add this service to the main docker-compose.yml:

```yaml
services:
  comment-reconciliation:
    # ... existing config
    depends_on:
      - reviewers-exec-summary
    networks:
      - app-network

  reviewers-exec-summary:
    build:
      context: ../reviewers-exec-summary
      dockerfile: Dockerfile
    container_name: reviewers-exec-summary-service
    ports:
      - "8001:8001"
    env_file:
      - ../reviewers-exec-summary/.env
    networks:
      - app-network
```

## Troubleshooting

### Service not starting

```bash
# Check logs
docker-compose logs reviewers-exec-summary

# Rebuild container
docker-compose build --no-cache
docker-compose up -d
```

### Connection issues

- Verify AWS credentials are correct
- Check OpenAI API key is valid
- Ensure S3 bucket is accessible
- Verify network connectivity

### Performance optimization

- Adjust worker count in `uvicorn` command
- Scale using Docker Compose:
  ```bash
  docker-compose up -d --scale reviewers-exec-summary=3
  ```

## License

Proprietary - Internal Use Only
