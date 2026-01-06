# Reviewers Executive Summary Project Description

## 1. Overview
The **Reviewers Executive Summary** project is a specialized microservice designed to generate comprehensive, neutral executive summaries for Traffic Control Plan (TCP) drawings. It leverages OpenAI's GPT-4o multimodal capabilities to analyze technical drawings along with their legend data and produce a structured JSON report. This report is intended to brief human reviewers on the key elements, traffic flow changes, and works zone definitions without providing compliance assessments.

## 2. Key Features
-   **AI-Powered Analysis**: Uses OpenAI's Assistant API (GPT-4o) to visually analyze TCP drawings and interpret their context using provided legend data.
-   **Automated Summarization**: Generates a detailed "Reviewer's Executive Summary" including:
    -   Purpose and setting of the TCP.
    -   Key elements (added, removed, maintained).
    -   Movement and flow descriptions for vehicles/pedestrians.
    -   Work zone definitions.
    -   Reviewer orientation notes.
-   **Structure Output**: Delivers the summary in a strict JSON format for easy downstream consumption.
-   **S3 Integration**: Seamlessly fetches inputs (drawings, legends) from and saves outputs (summaries, configs) to AWS S3.
-   **Robust Image Handling**: Automatically handles large images by compressing or resizing them to fit OpenAI's size limits before upload.
-   **Context Management**: Manages OpenAI Assistant and Thread lifecycles, persisting configuration to S3 to allow for potential context reuse (though currently optimizes for fresh context per summary).
-   **Resilience**: Includes retry logic for API calls and a fallback mechanism to standard Chat Completions if the Assistant API file upload fails.

## 3. Architecture

### 3.1 Tech Stack
-   **Language**: Python 3.11+
-   **Web Framework**: FastAPI
-   **Server**: Uvicorn
-   **AI Provider**: OpenAI API (pydantic-based client)
-   **Cloud Storage**: AWS S3 (via Boto3)
-   **Image Processing**: Pillow (PIL)
-   **Containerization**: Docker & Docker Compose

### 3.2 Project Structure
```
reviewers-exec-summary/
├── main.py                          # Entry point: FastAPI app, endpoints, and coordination
├── services/
│   ├── reviewers_exec_summary.py   # Core logic: OpenAI interaction, prompt engineering, result parsing
│   └── s3_service.py                # Infrastructure: S3 file fetching, saving, and presigned URL gen
├── requirements.txt                 # Dependencies
├── Dockerfile                       # Container definition
├── docker-compose.yml               # Service orchestration
└── .env                             # Configuration secrets (AWS & OpenAI keys)
```

## 4. API Reference

### POST `/generate-exec-summary`
Triggers the generation of an executive summary.

**Request Body:**
```json
{
  "drawing_s3_key": "documents/user123/submission1/rasterize/page1.png",
  "submission_key": "submission2",
  "page_num": "1",
  "old_submission_key": "submission1",
  "user_id": "user123",  // Optional
  "new_drawing_s3_key": "...", // Optional
  "legend_data": []      // Optional, fetched from S3 if missing
}
```

**Workflow:**
1.  **Validation**: parameters are checked, and missing data (like `legend_data`) is fetched from S3.
2.  **Image Prep**: The drawing image is retrieved from S3. If it exceeds OpenAI's 20MB limit, it is compressed (converted to JPEG, quality reduced) or resized until it fits.
3.  **AI Processing**:
    -   An OpenAI Assistant is retrieved or created.
    -   A new Thread is created.
    -   The image (as a file upload or URL) and legend data are sent with a specialized system prompt.
    -   The service polls for the run to complete.
4.  **Completion**: The generated JSON is parsed, validated, and saved to S3 at `documents/{user_id}/{submission_key}/reconciliation/{old_submission_key}/{page_num}/reviewers_exec_summary.json`.
5.  **Response**: The JSON data is returned to the client.

### GET `/health`
Returns the service health status.

## 5. Setup & Usage
1.  **Environment Variables**: Create a `.env` file with:
    -   `AWS_DEFAULT_REGION`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_S3_BUCKET`
    -   `OPENAI_API_KEY`
2.  **Docker**: Run `docker-compose up -d` to start the service on port `8001`.
3.  **Local Dev**: Install requirements with `pip install -r requirements.txt` and run `python main.py`.

## 6. Development Notes
-   **Prompt Engineering**: The core value lies in the system prompt defined in `services/reviewers_exec_summary.py`, which instructs the model to act as a Traffic Engineering expert and strictly follow the color-coding rules (Yellow=Remove, Red=Add, Black=Maintain).
-   **State persistence**: The service saves `config.json` containing `assistant_id` and `thread_id` to S3, allowing the application to link future requests to the same assistant context if needed.
