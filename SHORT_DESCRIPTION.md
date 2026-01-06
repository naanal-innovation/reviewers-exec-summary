# Reviewers Executive Summary - Short Description

## What is Reviewers Executive Summary?

**Reviewers Executive Summary** is a specialized AI-powered microservice designed to generate neutral, high-level summaries of Traffic Control Plan (TCP) drawings. By leveraging OpenAI's GPT-4o multimodal capabilities, it acts as an automated "first look" for reviewers, identifying proposed changes (additions, removals, maintenance) and explaining traffic flow impacts without offering compliance opinions.

## Key Features

🧠 **AI-Powered Analysis:**
- **Multimodal Understanding** - Combines visual analysis of TCP drawings with textual legend data
- **Context-Aware Interpreters** - Recognizes traffic engineering symbols and color codes (Red=New, Yellow=Remove, Black=Existing)
- **Neutral Reporting** - Strictly engineered prompts ensure non-judgmental, factual summaries

⚡ **Smart Processing:**
- **Automatic Image Optimization** - Resizes/compresses large engineering drawings to fit OpenAI limits
- **Robust Retry Logic** - Handles API transient failures automatically
- **Dual Fallback Mechanism** - Switches between File Upload (Assistant API) and Image URL (Chat API) methods for reliability

☁️ **Cloud Native:**
- **Seamless S3 Integration** - Direct fetching of inputs and saving of JSON outputs
- **State Persistence** - Saves Assistant/Thread configs to S3 for potential context reuse
- **Dockerized** - Ready for containerized deployment

## Technology Stack

### Core Technologies
- **FastAPI** - High-performance async web framework
- **Python 3.11+** - Modern language features
- **OpenAI API** - GPT-4o (Vision) via `openai` python client
- **Uvicorn** - ASGI server

### Infrastructure
- **AWS S3** - Storage for drawings, legends, reports, and configs
- **Boto3** - AWS SDK
- **Docker** - Containerization
- **Pillow (PIL)** - Advanced image processing

## Core Capabilities

✅ **Visual TCP Analysis**: Identifies work zones, tapers, and signage clusters  
✅ **Legend Mapping**: Translates abstract symbols into readable traffic terms  
✅ **Change Detection Summary**: Highlights what is changing vs. staying the same  
✅ **Structured Output**: Delivers predictable JSON for frontend display  
✅ **Large Image Handling**: Smart compression for high-res engineering plots  
✅ **Assistant Lifecycle**: Manages OpenAI Threads and Assistants efficiently  

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/generate-exec-summary` | POST | Generate a new executive summary |
| `/health` | GET | Service health check |

## Workflow

```
1. Receive Request (S3 keys for drawing & legend)
2. Fetch Data from S3
   ├─ Fetch Legend JSON
   └─ Fetch Drawing Image
3. Pre-process Image
   └─ Resize/Compress if > 20MB (OpenAI limit)
4. AI Processing (GPT-4o)
   ├─ Create/Load Assistant & Thread
   ├─ Upload Image & Legend
   └─ Run specialized "Traffic Expert" prompt
5. Response Handling
   ├─ Parse JSON from AI response
   └─ Save result to S3
```

## Output Structure

**S3 Path:**
`documents/{userId}/{submissionKey}/reconciliation/{oldSubmissionKey}/{pageNum}/reviewers_exec_summary.json`

**JSON Format:**
```json
{
  "title": "Reviewer's Executive Summary...",
  "purpose_and_setting": "...",
  "key_elements_present": ["..."],
  "movement_and_flow": "...",
  "work_zone_definition": "...",
  "temporary_alignment_and_controls": ["..."],
  "reviewer_orientation_notes": ["..."]
}
```

## Integration Points

🔗 **Comment Reconciliation Service** - Main consumer of this service  
🔗 **AWS S3** - Source of truth for all documents  
🔗 **OpenAI Platform** - Engine for intelligence  

## Docker Deployment

```yaml
Service: reviewers-exec-summary
Port: 8001:8001
Environment:
  - AWS Credentials
  - OPENAI_API_KEY
Networks:
  - app-network
```
