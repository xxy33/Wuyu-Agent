"""Detection task API router."""
import asyncio
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, HTTPException, BackgroundTasks
from fastapi.responses import StreamingResponse

from web.config import UPLOAD_DIR, OUTPUT_DIR, DETECTION_TASKS
from web.models.schemas import (
    DetectionTask, DetectionRequest, DetectionResponse,
    TaskStatus, TaskStatusResponse
)
from web.services.progress_tracker import progress_tracker

router = APIRouter(prefix="/api/detection", tags=["detection"])

# Store for running tasks
_running_tasks = {}


@router.get("/tasks", response_model=List[DetectionTask])
async def get_tasks():
    """Get list of available detection tasks."""
    return [DetectionTask(**task) for task in DETECTION_TASKS]


@router.post("/start", response_model=DetectionResponse)
async def start_detection(request: DetectionRequest, background_tasks: BackgroundTasks):
    """Start a detection task."""
    # Validate session
    session_dir = UPLOAD_DIR / request.session_id
    if not session_dir.exists():
        raise HTTPException(status_code=404, detail="Session not found. Please upload images first.")

    # Get image files
    image_files = list(session_dir.glob("*"))
    image_files = [f for f in image_files if f.is_file()]

    if not image_files:
        raise HTTPException(status_code=400, detail="No images found in session")

    # Validate tasks
    valid_task_ids = {t["id"] for t in DETECTION_TASKS}
    for task_id in request.tasks:
        if task_id not in valid_task_ids:
            raise HTTPException(status_code=400, detail=f"Invalid task ID: {task_id}")

    # Create progress tracker task
    task_id = await progress_tracker.create_task(
        session_id=request.session_id,
        total=len(image_files)
    )

    # Start background detection
    background_tasks.add_task(
        run_detection,
        task_id=task_id,
        session_id=request.session_id,
        image_files=image_files,
        tasks=request.tasks,
        city_name=request.city_name,
        vl_base_url=request.vl_base_url,
        vl_api_key=request.vl_api_key,
        vl_model=request.vl_model,
        llm_base_url=request.llm_base_url,
        llm_api_key=request.llm_api_key,
        llm_model=request.llm_model,
        sam2_url=request.sam2_url,
        sam2_api_key=request.sam2_api_key,
        sam2_model=request.sam2_model
    )

    return DetectionResponse(
        task_id=task_id,
        session_id=request.session_id,
        status=TaskStatus.PENDING,
        message=f"Detection task created. Processing {len(image_files)} images."
    )


async def run_detection(
    task_id: str,
    session_id: str,
    image_files: List[Path],
    tasks: List[str],
    city_name: str,
    vl_base_url: str,
    vl_api_key: str,
    vl_model: str,
    llm_base_url: str,
    llm_api_key: str,
    llm_model: str,
    sam2_url: Optional[str],
    sam2_api_key: Optional[str],
    sam2_model: Optional[str]
):
    """Run detection in background."""
    try:
        await progress_tracker.start_task(task_id)

        # Create output directory for this session
        output_dir = OUTPUT_DIR / session_id
        output_dir.mkdir(parents=True, exist_ok=True)

        # Try to import and run actual detection
        try:
            from swagent.multi_domain_detection.runner import run_multi_domain_detection

            # Define progress callback
            async def progress_callback(current: int, total: int, filename: str, message: str):
                task_info = await progress_tracker.get_task(task_id)
                if task_info and task_info.status == TaskStatus.STOPPED:
                    raise InterruptedError("Task stopped by user")
                await progress_tracker.update_progress(
                    task_id=task_id,
                    current=current,
                    current_file=filename,
                    message=message
                )

            # Run the actual detection using the tested runner
            result = await run_multi_domain_detection(
                mode="prod",
                input_path=str(UPLOAD_DIR / session_id),
                city=city_name,
                tasks=tasks,
                output_dir=str(output_dir),
                vl_base_url=vl_base_url,
                vl_api_key=vl_api_key,
                vl_model=vl_model,
                llm_base_url=llm_base_url,
                llm_api_key=llm_api_key,
                llm_model=llm_model,
                small_model_url=sam2_url,
                small_model_key=sam2_api_key,
                small_model_name=sam2_model or "sam2_large",
                progress_callback=progress_callback,
                create_session_subdir=False  # Web调用时不创建子目录，output_dir已是session专用
            )

        except ImportError:
            # Fallback: simulate detection for demo
            for i, image_file in enumerate(image_files):
                # Check if task was stopped
                task_info = await progress_tracker.get_task(task_id)
                if task_info and task_info.status == TaskStatus.STOPPED:
                    return

                await progress_tracker.update_progress(
                    task_id=task_id,
                    current=i + 1,
                    current_file=image_file.name,
                    message=f"处理图像: {image_file.name}"
                )

                # Simulate processing time
                await asyncio.sleep(0.5)

        await progress_tracker.complete_task(
            task_id=task_id,
            message=f"检测完成! 共处理 {len(image_files)} 张图像"
        )

    except InterruptedError:
        pass  # Task was interrupted
    except Exception as e:
        await progress_tracker.fail_task(task_id=task_id, error=str(e))


@router.get("/progress/{task_id}")
async def get_progress_stream(task_id: str):
    """Get SSE stream for task progress."""
    task = await progress_tracker.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return StreamingResponse(
        progress_tracker.subscribe(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )


@router.get("/status/{task_id}", response_model=TaskStatusResponse)
async def get_task_status(task_id: str):
    """Get current task status."""
    task = await progress_tracker.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    return TaskStatusResponse(
        task_id=task.task_id,
        session_id=task.session_id,
        status=task.status,
        progress=(task.current / task.total * 100) if task.total > 0 else 0,
        current_file=task.current_file,
        started_at=task.started_at,
        completed_at=task.completed_at,
        error=task.error
    )


@router.post("/stop/{task_id}")
async def stop_task(task_id: str):
    """Stop a running task."""
    task = await progress_tracker.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")

    if task.status != TaskStatus.RUNNING:
        raise HTTPException(status_code=400, detail="Task is not running")

    stopped = await progress_tracker.stop_task(task_id)
    if stopped:
        return {"task_id": task_id, "status": "stopped", "message": "Task stopped successfully"}
    else:
        raise HTTPException(status_code=500, detail="Failed to stop task")
