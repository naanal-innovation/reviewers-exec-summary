from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional, Dict, Any
import os
from dotenv import load_dotenv
import logging

from services.reviewers_exec_summary import ReviewersExecSummaryGenerator
from services.s3_service import S3Service

load_dotenv()

app = FastAPI(title="Reviewers Executive Summary Generator Service")

# Configure CORS
origins = [
    "http://localhost:3000",
    "http://localhost:8000",
    "https://gollab3aws.bizzaard.com",
    "https://gollab3aws.bizzaard.com:443"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize services
s3_service = S3Service()
reviewers_exec_summary_generator = ReviewersExecSummaryGenerator()

class ExecSummaryRequest(BaseModel):
    drawing_s3_key: str
    submission_key: str
    page_num: str
    user_id: Optional[str] = None
    new_drawing_s3_key: Optional[str] = None
    legend_data: Optional[list] = None

@app.post("/generate-exec-summary")
async def generate_executive_summary(request: ExecSummaryRequest):
    """Generate Reviewer's Executive Summary for a TCP drawing"""
    try:
        bucket_name = os.getenv('AWS_S3_BUCKET')
        if not bucket_name:
            raise HTTPException(status_code=500, detail="AWS_S3_BUCKET not found in environment")

        # Extract user_id if not provided
        user_id = request.user_id
        if not user_id:
            parts = request.drawing_s3_key.split('/')
            user_id = parts[1] if len(parts) > 1 else "unknown"

        # Fetch legend data if not provided
        legend_data = request.legend_data
        if not legend_data:
            try:
                legend_files = await s3_service.fetch_legend_files(bucket_name, user_id, request.submission_key)
                # Extract page number and get corresponding legend
                page_num_str = request.page_num.replace('page', '') if 'page' in request.page_num else request.page_num
                page_legend = legend_files.get(page_num_str, {})
                legend_data = page_legend.get('openai_detections', [])
            except Exception as e:
                logger.warning(f"Could not fetch legend data: {e}")
                legend_data = []

        # Generate executive summary
        exec_summary_response = await reviewers_exec_summary_generator.reviewers_exec_summary(
            s3_service=s3_service,
            bucket_name=bucket_name,
            drawing_s3_key=request.drawing_s3_key,
            legend_data=legend_data,
            submission_key=request.submission_key,
            page_num=request.page_num,
            new_drawing_s3_key=request.new_drawing_s3_key
        )

        return {
            "status": "success",
            "data": exec_summary_response
        }

    except Exception as e:
        logger.error(f"Executive summary generation failed: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate executive summary: {str(e)}")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "reviewers-exec-summary"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
