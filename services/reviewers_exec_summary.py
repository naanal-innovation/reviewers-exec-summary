import json
import os
import asyncio
import time
from dotenv import load_dotenv
from langfuse import observe
from langfuse.openai import openai

load_dotenv()

class ReviewersExecSummaryGenerator:
    def __init__(self):
        api_key = os.getenv('OPENAI_API_KEY')
        if not api_key:
            raise ValueError("OPENAI_API_KEY environment variable not set")
        self.client = openai.AsyncOpenAI(api_key=api_key)

    async def _get_config_s3_path(self, s3_service, user_id: str, current_submission_key: str, old_submission_key: str, page: str) -> str:
        """Generate S3 path for storing assistant/thread config"""
        # Always extract just the numeric part from page parameter
        if page.startswith('page'):
            page_num = page[4:]  # Remove 'page' prefix to get just the number
        else:
            page_num = page

        # Always use numeric format for config path
        config_path = f"documents/{user_id}/{current_submission_key}/reconciliation/{old_submission_key}/{page_num}/config.json"
        print(f"[DEBUG] Using config path: {config_path}")
        return config_path

    async def _config_exists(self, s3_service, bucket_name: str, config_path: str) -> bool:
        """Check if config exists in S3"""
        try:
            s3_service.s3_client.head_object(Bucket=bucket_name, Key=config_path)
            return True
        except:
            return False

    async def _load_assistant_config(self, s3_service, bucket_name: str, config_path: str) -> dict:
        """Load assistant and thread IDs from S3 config"""
        try:
            response = s3_service.s3_client.get_object(Bucket=bucket_name, Key=config_path)
            config_data = json.loads(response['Body'].read())
            print(f"[DEBUG] Loaded existing config from {config_path}")
            return config_data
        except Exception as e:
            print(f"[DEBUG] Config loading error at {config_path}: {e}")
            return {}

    async def _save_assistant_config(self, s3_service, bucket_name: str, config_path: str, assistant_id: str, thread_id: str) -> None:
        """Save assistant and thread IDs to S3 config"""
        try:
            config_data = {
                "assistant_id": assistant_id,
                "thread_id": thread_id,
                "created_at": str(int(time.time())),
                "model": "gpt-4o"
            }

            config_json = json.dumps(config_data, indent=2)
            s3_service.s3_client.put_object(
                Bucket=bucket_name,
                Key=config_path,
                Body=config_json.encode('utf-8'),
                ContentType='application/json'
            )
            print(f"Saved assistant config to {config_path}")
        except Exception as e:
            print(f"Failed to save config to {config_path}: {e}")

    async def _cleanup_expired_files_in_thread(self, thread_id: str) -> None:
        """Remove expired file references while preserving all text context"""
        try:
            messages = await self.client.beta.threads.messages.list(thread_id=thread_id, limit=100)

            for message in messages.data:
                if message.role == "user":
                    has_expired_files = False
                    text_content = []
                    valid_files = []

                    # Extract text content and check for expired files
                    for content in message.content:
                        if content.type == "text":
                            text_content.append(content.text.value)
                        elif content.type == "image_file":
                            try:
                                await self.client.files.retrieve(content.image_file.file_id)
                                # File is still valid, keep it
                                valid_files.append(content)
                            except:
                                has_expired_files = True
                                print(f"[DEBUG] Found expired file: {content.image_file.file_id}")

                    # If there are expired files, recreate the message with valid content only
                    if has_expired_files:
                        try:
                            # Delete the old message
                            await self.client.beta.threads.messages.delete(
                                thread_id=thread_id,
                                message_id=message.id
                            )

                            # Recreate message with text content and any valid files
                            new_content = []
                            if text_content:
                                new_content.append({
                                    "type": "text",
                                    "text": "\n".join(text_content)
                                })

                            # Add back any files that are still valid
                            for valid_file in valid_files:
                                new_content.append({
                                    "type": "image_file",
                                    "image_file": {"file_id": valid_file.image_file.file_id}
                                })

                            if new_content:
                                await self.client.beta.threads.messages.create(
                                    thread_id=thread_id,
                                    role="user",
                                    content=new_content
                                )
                                print(f"[DEBUG] Preserved context from message with expired files")

                        except Exception as e:
                            print(f"[DEBUG] Could not recreate message: {e}")

        except Exception as e:
            print(f"[DEBUG] Error during smart cleanup: {e}")

    async def _save_response_to_s3(self, s3_service, bucket_name: str, user_id: str, current_submission_key: str, old_submission_key: str, page_num: str, response_data: dict) -> str:
        """Save the reviewers exec summary response to S3"""
        try:
            # Extract just the numeric part from page parameter
            if page_num.startswith('page'):
                page_numeric = page_num[4:]  # Remove 'page' prefix to get just the number
            else:
                page_numeric = page_num

            # Generate S3 path: reconciliation/submission51/1/reviewers_exec_summary.json
            s3_path = f"documents/{user_id}/{current_submission_key}/reconciliation/{old_submission_key}/{page_numeric}/reviewers_exec_summary.json"

            # Clean and extract JSON from response text
            import re
            report_text = response_data.get("report_text", "")

            try:
                # Use regex to extract JSON from markdown code blocks or find direct JSON
                json_match = re.search(r'```json\s*(.*?)\s*```', report_text, re.DOTALL)
                if json_match:
                    # Found JSON in backticks
                    json_content = json_match.group(1).strip()
                    parsed_json = json.loads(json_content)
                    response_json = json.dumps(parsed_json, indent=2)
                    print(f"[DEBUG] Extracted JSON from markdown backticks")
                elif report_text.strip().startswith("{"):
                    # Try direct JSON parsing
                    parsed_json = json.loads(report_text.strip())
                    response_json = json.dumps(parsed_json, indent=2)
                    print(f"[DEBUG] Parsed direct JSON response")
                else:
                    # No JSON found, keep original format
                    response_json = json.dumps(response_data, indent=2)

            except (json.JSONDecodeError, Exception) as e:
                print(f"[DEBUG] JSON parsing failed, using original format: {e}")
                response_json = json.dumps(response_data, indent=2)

            # Upload to S3
            s3_service.s3_client.put_object(
                Bucket=bucket_name,
                Key=s3_path,
                Body=response_json.encode('utf-8'),
                ContentType='application/json'
            )

            print(f"[DEBUG] ✅ Saved reviewers exec summary to S3: {s3_path}")
            return s3_path
        except Exception as e:
            print(f"[DEBUG] ❌ Failed to save reviewers exec summary to S3: {e}")
            return None

    @observe(name="reviewers-exec-summary", capture_input=False, capture_output=False)
    async def reviewers_exec_summary(self, s3_service, bucket_name: str, drawing_s3_key: str, legend_data: dict, submission_key: str, page_num: str, old_submission_key: str, new_drawing_s3_key: str = None) -> dict:
        """
        Generate a Reviewer's Executive Summary (JSON) for a TCP drawing.
        Inputs:
        - drawing_path: path to TCP drawing image (PNG/JPG/WEBP/PDF page)
        - legend_json_path: path to legend JSON mapping
        - system_instruction: system prompt (with color code, grid definition, and output schema)
        - submission_id: identifier for this submission
        Output:
        - Parsed JSON summary from LLM
        """

        # Create submission_id in format submission_key/page_num
        submission_id = f"{submission_key}/{page_num}"

        # Extract user_id from drawing_s3_key
        path_parts = drawing_s3_key.split('/')
        user_id = path_parts[1] if len(path_parts) > 1 else "unknown"

        # Use submission_key parameter as current_submission_key
        current_submission_key = submission_key

        # old_submission_key is now passed as a parameter (no longer extracted from filename)

        # Generate S3 config path
        config_path = await self._get_config_s3_path(s3_service, user_id, current_submission_key, old_submission_key, page_num)
        print(f"[DEBUG] Thread creation - user_id: {user_id}, current_submission_key: {current_submission_key}, old_submission_key: {old_submission_key}, page_num: {page_num}")
        print(f"[DEBUG] Thread creation - config_path: {config_path}")

        # Check if config exists first, then load or create new
        config_exists = await self._config_exists(s3_service, bucket_name, config_path)

        if config_exists:
            print(f"[DEBUG] Config exists, loading existing assistant config")
            config = await self._load_assistant_config(s3_service, bucket_name, config_path)
            assistant_id = config.get('assistant_id')
        else:
            print(f"[DEBUG] No config found at {config_path}, will create new assistant and thread")
            config = {}
            assistant_id = None

        # The bucket is private, so OpenAI reads the drawing through a presigned
        # URL (used when the file upload below fails). S3 serves the object's
        # stored content type, which may be PNG or WebP.
        drawing_url = s3_service.s3_client.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket_name, 'Key': drawing_s3_key},
            ExpiresIn=3600,
        )
        print(f"[DEBUG] Using presigned S3 URL for key: {drawing_s3_key}")
        print(f"[DEBUG] Using bucket: {bucket_name}, key: {drawing_s3_key}")

        # Force file upload to ensure thread compatibility with intent parser
        url_accessible = False
        print(f"[DEBUG] Forcing file upload for main drawing to ensure thread compatibility")

        # Test if the object exists in S3 directly
        try:
            s3_response = s3_service.s3_client.head_object(Bucket=bucket_name, Key=drawing_s3_key)
            print(f"[DEBUG] ✅ Object exists in S3: size={s3_response.get('ContentLength', 'unknown')} bytes")
        except Exception as e:
            print(f"[DEBUG] ❌ Object not found in S3: {e}")
            return {"report_text": f"Error: Drawing image not found in S3 at {drawing_s3_key}"}

        # If presigned URL fails, upload image as file to OpenAI for Assistant API
        openai_file_id = None
        if not url_accessible:
            print(f"[DEBUG] Presigned URL not accessible. Uploading image as file to OpenAI for Assistant API...")
            try:
                # Download image from S3
                s3_obj = s3_service.s3_client.get_object(Bucket=bucket_name, Key=drawing_s3_key)
                image_data = s3_obj['Body'].read()
                original_size = len(image_data)
                print(f"[DEBUG] Original image size: {original_size / (1024*1024):.2f} MB")

                # Determine image format and filename
                content_type = s3_obj.get('ContentType', 'image/webp')
                if 'webp' in drawing_s3_key.lower() or 'webp' in content_type:
                    image_format = 'webp'
                elif 'png' in drawing_s3_key.lower() or 'png' in content_type:
                    image_format = 'png'
                elif 'jpg' in drawing_s3_key.lower() or 'jpeg' in drawing_s3_key.lower() or 'jpeg' in content_type:
                    image_format = 'jpeg'
                else:
                    image_format = 'webp'  # default

                # Compress image if it exceeds OpenAI's 20MB limit
                import io
                from PIL import Image

                MAX_SIZE_MB = 20
                MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024

                if original_size > MAX_SIZE_BYTES:
                    print(f"[DEBUG] Image exceeds {MAX_SIZE_MB}MB limit, compressing...")

                    # Increase Pillow's decompression bomb limit for legitimate large images
                    # Default is ~89MP, we'll allow up to 500MP for high-res technical drawings
                    Image.MAX_IMAGE_PIXELS = 500_000_000

                    # Load image with PIL
                    img = Image.open(io.BytesIO(image_data))
                    print(f"[DEBUG] Original dimensions: {img.size[0]}x{img.size[1]}, mode: {img.mode}")

                    # Convert to RGB if necessary (required for JPEG)
                    if img.mode in ('RGBA', 'P', 'LA'):
                        # Create white background for transparency
                        background = Image.new('RGB', img.size, (255, 255, 255))
                        if img.mode == 'P':
                            img = img.convert('RGBA')
                        background.paste(img, mask=img.split()[-1] if img.mode in ('RGBA', 'LA') else None)
                        img = background
                    elif img.mode != 'RGB':
                        img = img.convert('RGB')

                    # Try compression with progressively higher compression
                    quality = 85
                    output_format = 'JPEG'  # JPEG compresses better than PNG/WebP for photos

                    while quality > 20:
                        output = io.BytesIO()
                        img.save(output, format=output_format, quality=quality, optimize=True)
                        compressed_size = output.tell()

                        if compressed_size <= MAX_SIZE_BYTES:
                            print(f"[DEBUG] Compressed to {compressed_size / (1024*1024):.2f} MB with quality={quality}")
                            image_data = output.getvalue()
                            image_format = 'jpeg'
                            break

                        quality -= 10

                    # If still too large after max compression, resize the image
                    if compressed_size > MAX_SIZE_BYTES:
                        print(f"[DEBUG] Still too large, resizing image...")
                        scale_factor = 0.8

                        while compressed_size > MAX_SIZE_BYTES and scale_factor > 0.3:
                            new_width = int(img.size[0] * scale_factor)
                            new_height = int(img.size[1] * scale_factor)
                            resized_img = img.resize((new_width, new_height), Image.Resampling.LANCZOS)

                            output = io.BytesIO()
                            resized_img.save(output, format=output_format, quality=75, optimize=True)
                            compressed_size = output.tell()

                            if compressed_size <= MAX_SIZE_BYTES:
                                print(f"[DEBUG] Resized to {new_width}x{new_height} and compressed to {compressed_size / (1024*1024):.2f} MB")
                                image_data = output.getvalue()
                                image_format = 'jpeg'
                                break

                            scale_factor -= 0.1

                    print(f"[DEBUG] Final compressed size: {len(image_data) / (1024*1024):.2f} MB")

                # Create a file-like object for upload
                image_file = io.BytesIO(image_data)
                image_file.name = f"drawing.{image_format}"

                # Upload file to OpenAI
                file_response = await self.client.files.create(
                    file=image_file,
                    purpose="vision"
                )
                openai_file_id = file_response.id
                print(f"[DEBUG] ✅ Uploaded image to OpenAI: file_id={openai_file_id}, size={len(image_data)} bytes")

            except Exception as e:
                print(f"[DEBUG] ❌ File upload to OpenAI failed: {e}")
                return {"report_text": f"Error: Could not upload image to OpenAI: {e}"}

        # System instruction prompt
        system_prompt = f"""
            You are a Traffic Engineering expert summarizer.
            Goal: Produce a concise "Reviewer's Executive Summary" that briefs a human reviewer about the submitted Traffic Control Plan (TCP) drawing.

            Inputs you will receive:
            1) A TCP drawing (image).
            2) Legend JSON mapping symbols → traffic-engineering terminology.
            3) (Optional) Grid definition (page-scoped A1…F6). If not provided, assume a 512x512 px grid and label columns A.. and rows 1..

            Important Context:
            In the proposed TCP, colors indicate the status of traffic features:
            • Yellow → features proposed to be REMOVED
            • Red → features proposed to be ADDED
            • Black → features proposed to be MAINTAINED (existing)

            What to do:
            Use legend JSON terms for describing symbols (do NOT re-extract or redefine legends).
            Where possible, link observations to grid cells (e.g., "D3 eastbound approach").
            Explain the role of **removed (yellow)**, **added (red)**, and **maintained (black)** features in shaping the design intent.
            Write a neutral, context-only executive summary (no evaluation, no pass/fail, no severity, no recommendations).

            Output Format:
            Always return valid JSON with the following structure:

            {{
            "title": "Reviewer's Executive Summary — TCP Submission {submission_id}",
            "purpose_and_setting": "One or two sentences summarizing the purpose of the TCP.",
            "key_elements_present": [
                "Bullet-style descriptions using legend terms, grid cells, and if possible color status (added/red, removed/yellow, maintained/black)"
            ],
            "movement_and_flow": "2-4 sentences summarizing how vehicles, pedestrians, and cyclists are routed.",
            "work_zone_definition": "1-2 sentences describing how the work area is delineated.",
            "temporary_alignment_and_controls": [
                "Bullets describing temporary tapers, diversions, directional guidance, pedestrian/cyclist continuity, with references to color-coded status where applicable"
            ],
            "reviewer_orientation_notes": [
                "Neutral, non-judgmental pointers to where attention may be needed (e.g., dense signage cluster at E3, pedestrian crossing shifted at C3, lane removal in yellow at D3)."
            ]
            }}

            Style & Constraints:
            Neutral briefing tone (informational, not evaluative).
            Do NOT assess compliance, do NOT use words like Pass/Fail, Met/Not Met, Risk/Severity, or give recommendations.
            Content should be concise (~200-300 words total).
            If something cannot be inferred, output: "not inferred from provided inputs".
            """

        # Use Assistant API for multimodal input (drawing + legend JSON)
        try:
            # Step 1: Always create a new thread for exec summary (fresh context)
            # Check if config exists first, then load or create new
            config_exists = await self._config_exists(s3_service, bucket_name, config_path)

            if config_exists:
                print(f"[DEBUG] Config exists, loading existing assistant config")
                config = await self._load_assistant_config(s3_service, bucket_name, config_path)
                assistant_id = config.get('assistant_id')
            else:
                print(f"[DEBUG] No config found, will create new assistant and thread")
                config = {}
                assistant_id = None

            # Try to reuse existing assistant but always create new thread
            assistant = None
            if assistant_id:
                try:
                    assistant = await self.client.beta.assistants.retrieve(assistant_id)
                    print(f"Using existing assistant: {assistant_id}")
                except:
                    print(f"Assistant {assistant_id} not found, creating new one")
                    assistant = None

            # Create new assistant if needed
            if not assistant:
                try:
                    print(f"[DEBUG] Creating new assistant...")
                    assistant = await self.client.beta.assistants.create(
                        name="TCP Reviewers Executive Summary Generator",
                        instructions=system_prompt,
                        model="gpt-4o",  # Use gpt-4o for vision capabilities
                        tools=[{"type": "code_interpreter"}]  # Enable code interpreter for JSON processing
                    )
                    print(f"[DEBUG] ✅ Created new assistant: {assistant.id}")
                except Exception as e:
                    print(f"[DEBUG] ❌ Failed to create assistant: {e}")
                    raise e

            # Always create a new thread for fresh context
            try:
                print(f"[DEBUG] Creating new thread...")
                thread = await self.client.beta.threads.create()
                print(f"[DEBUG] ✅ Created new thread for exec summary: {thread.id}")
            except Exception as e:
                print(f"[DEBUG] ❌ Failed to create thread: {e}")
                raise e

            # Save config to S3 with new thread ID
            try:
                print(f"[DEBUG] Saving config to S3...")
                await self._save_assistant_config(s3_service, bucket_name, config_path, assistant.id, thread.id)
                print(f"[DEBUG] ✅ Config saved to S3: {config_path}")
            except Exception as e:
                print(f"[DEBUG] ❌ Failed to save config: {e}")
                raise e

            # Step 3: Skip cleanup since this is a new thread

            # Debug existing messages in thread after cleanup
            existing_messages = await self.client.beta.threads.messages.list(thread_id=thread.id, limit=5)
            print(f"[DEBUG] Thread {thread.id} has {len(existing_messages.data)} existing messages after cleanup")
            for i, msg in enumerate(existing_messages.data[-3:]):  # Show last 3 messages
                print(f"[DEBUG] Message {i}: role={msg.role}, content_types={[c.type for c in msg.content]}")
                for j, content in enumerate(msg.content):
                    if content.type == "image_url":
                        print(f"[DEBUG]   Image URL: {content.image_url.url[:100]}...")
                    elif content.type == "image_file":
                        print(f"[DEBUG]   Image file: {content.image_file.file_id}")

            # Step 4: Add message with legend data and image to the thread
            message_content = [
                {
                    "type": "text",
                    "text": f"Legend JSON:\n{json.dumps(legend_data, indent=2)}\n\nHere is the submitted TCP drawing for review:"
                }
            ]

            # Add image either as URL or uploaded file
            if openai_file_id:
                print(f"[DEBUG] Sending uploaded file to Assistant API: file_id={openai_file_id}")
                message_content.append({
                    "type": "image_file",
                    "image_file": {"file_id": openai_file_id}
                })
            else:
                print(f"[DEBUG] Sending image URL to Assistant API: {drawing_url}")
                message_content.append({
                    "type": "image_url",
                    "image_url": {"url": drawing_url}
                })

            await self.client.beta.threads.messages.create(
                thread_id=thread.id,
                role="user",
                content=message_content
            )

            # Step 4: Create run with retry mechanism for concurrent access
            max_retries = 5
            retry_count = 0
            run = None

            while retry_count < max_retries:
                try:
                    # Check for existing active runs and wait for completion
                    print(f"[ExecSummary] Checking for active runs (attempt {retry_count + 1}/{max_retries})...")
                    existing_runs = await self.client.beta.threads.runs.list(thread_id=thread.id, limit=10)

                    # Wait for any active runs to complete
                    active_run_found = False
                    for existing_run in existing_runs.data:
                        if existing_run.status in ['queued', 'in_progress', 'requires_action']:
                            active_run_found = True
                            print(f"[ExecSummary] Waiting for existing run {existing_run.id} (status: {existing_run.status}) to complete...")
                            while existing_run.status in ['queued', 'in_progress', 'requires_action']:
                                await asyncio.sleep(3)
                                existing_run = await self.client.beta.threads.runs.retrieve(
                                    thread_id=thread.id,
                                    run_id=existing_run.id
                                )
                                print(f"[ExecSummary] Run {existing_run.id} status: {existing_run.status}")
                            print(f"[ExecSummary] Existing run {existing_run.id} completed with status: {existing_run.status}")
                            break

                    # Small delay to avoid race conditions
                    if not active_run_found:
                        await asyncio.sleep(1)

                    # Try to create the run
                    print(f"[ExecSummary] Creating new run...")
                    run = await self.client.beta.threads.runs.create(
                        thread_id=thread.id,
                        assistant_id=assistant.id
                    )
                    print(f"[ExecSummary] Successfully created run {run.id}")
                    break  # Success, exit retry loop

                except Exception as e:
                    error_msg = str(e)
                    if "already has an active run" in error_msg:
                        retry_count += 1
                        wait_time = min(2 ** retry_count, 10)  # Exponential backoff, max 10s
                        print(f"[ExecSummary] Thread busy, retrying in {wait_time}s (attempt {retry_count}/{max_retries})...")
                        await asyncio.sleep(wait_time)
                    else:
                        print(f"[ExecSummary] Non-retryable error creating run: {e}")
                        raise e

            if run is None:
                raise Exception(f"[ExecSummary] Failed to create run after {max_retries} attempts")

            # Step 5: Wait for completion and retrieve response
            while run.status in ['queued', 'in_progress']:
                await asyncio.sleep(1)
                run = await self.client.beta.threads.runs.retrieve(
                    thread_id=thread.id,
                    run_id=run.id
                )

            if run.status == 'completed':
                # Get the messages from the thread
                messages = await self.client.beta.threads.messages.list(
                    thread_id=thread.id
                )

                # Debug: Print all messages to see what was sent/received
                print(f"[DEBUG] Total messages in thread: {len(messages.data)}")
                for i, msg in enumerate(messages.data):
                    print(f"[DEBUG] Message {i}: role={msg.role}, content_count={len(msg.content)}")
                    if msg.role == "user" and len(msg.content) > 1:
                        for j, content in enumerate(msg.content):
                            print(f"[DEBUG]   Content {j}: type={content.type}")
                            if content.type == "image_url":
                                print(f"[DEBUG]   Image URL sent: {content.image_url.url[:100]}...")

                # Get the latest assistant message
                assistant_message = None
                for message in messages.data:
                    if message.role == "assistant":
                        assistant_message = message
                        break

                if assistant_message and assistant_message.content:
                    output_text = assistant_message.content[0].text.value
                    print(f"[DEBUG] Assistant response length: {len(output_text)} characters")
                    # Check if response mentions image-related terms
                    image_terms = ['drawing', 'image', 'visual', 'see', 'shown', 'depicted', 'traffic', 'TCP']
                    found_terms = [term for term in image_terms if term.lower() in output_text.lower()]
                    print(f"[DEBUG] Image-related terms found in response: {found_terms}")
                else:
                    output_text = "No response from assistant"
            else:
                output_text = f"Assistant run failed with status: {run.status}"
                print(f"[DEBUG] Run failed. Last error: {getattr(run, 'last_error', 'No error details')}")

            # Note: We keep the assistant and thread alive for reuse
            # They are stored in S3 config for future requests

            # Keep uploaded file for later use by intent parser in the same thread
            if openai_file_id:
                print(f"[DEBUG] ℹ️ Keeping uploaded file for thread reuse: {openai_file_id}")

            print(f"Reviewers Executive Summary Generator response: {output_text}")

            # Save response to S3 before returning
            response_data = {
                "report_text": output_text,
                "assistant_id": assistant.id,
                "thread_id": thread.id
            }
            await self._save_response_to_s3(s3_service, bucket_name, user_id, current_submission_key, old_submission_key, page_num, response_data)

            return response_data

        except Exception as e:
            print(f"Assistant API error: {e}")

            # Keep uploaded file even on fallback - it might be useful for future requests
            if openai_file_id:
                print(f"[DEBUG] ℹ️ Keeping uploaded file even on fallback: {openai_file_id}")

            # Fallback to chat completions if assistant API fails (only works with URLs, not uploaded files)
            if not openai_file_id:  # Only fallback if we have a URL, not an uploaded file
                print(f"[DEBUG] Fallback: Sending image URL to OpenAI chat completions: {drawing_url}")
                response = await self.client.chat.completions.create(
                    model="gpt-4o",
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": f"Legend JSON:\n{json.dumps(legend_data, indent=2)}"},
                        {
                            "role": "user",
                            "content": [
                                {"type": "text", "text": "Here is the submitted TCP drawing for review:"},
                                {"type": "image_url", "image_url": {"url": drawing_url}}
                            ]
                        }
                    ],
                    temperature=0.2,
                    max_tokens=3000,
                    seed=42
                )
                output_text = response.choices[0].message.content
                print(f"[DEBUG] Fallback response length: {len(output_text)} characters")
                # Check if response mentions image-related terms
                image_terms = ['drawing', 'image', 'visual', 'see', 'shown', 'depicted', 'traffic', 'TCP']
                found_terms = [term for term in image_terms if term.lower() in output_text.lower()]
                print(f"[DEBUG] Fallback image-related terms found: {found_terms}")
                print(f"Fallback to chat completions: {output_text}")

                # Save response to S3 before returning
                response_data = {"report_text": output_text}
                await self._save_response_to_s3(s3_service, bucket_name, user_id, current_submission_key, old_submission_key, page_num, response_data)

                return response_data
            else:
                # If we used file upload and Assistant API failed, we can't fallback to Chat Completions
                error_text = f"Error: Assistant API failed and cannot fallback with uploaded file: {str(e)}"

                # Save error response to S3 before returning
                response_data = {"report_text": error_text}
                await self._save_response_to_s3(s3_service, bucket_name, user_id, current_submission_key, old_submission_key, page_num, response_data)

                return response_data


if __name__ == "__main__":
    import asyncio
    from PIL import Image

    async def main():
        # Example usage with local files for testing
        print("Testing Reviewers Executive Summary Generator...")

        try:
            generator = ReviewersExecSummaryGenerator()

            # Load test image and legend (you can modify these paths as needed)
            drawing_image = Image.open("test_drawing.webp")  # Update with actual test file
            with open("test_legend.json", "r") as f:
                legend_data = json.load(f)

            report = await generator.reviewers_exec_summary(
                drawing_image=drawing_image,
                legend_data=legend_data
            )

            # Save output to file
            with open("reviewers_exec_summary.txt", "w", encoding="utf-8") as f:
                f.write(report["report_text"])

            print("✅ LLM-based Reviewers Executive Summary generated: reviewers_exec_summary.txt")

        except FileNotFoundError as e:
            print(f"Test files not found: {e}")
            print("Note: This function is integrated into the main pipeline.")
            print("For testing, place test_drawing.webp and test_legend.json in the same directory.")

    asyncio.run(main())
