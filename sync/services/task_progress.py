# sync/services/task_progress.py
import logging
from typing import Optional, Dict, Any
from django.core.exceptions import ValidationError
from sync.models.task_progress import TaskProgress

logger = logging.getLogger(__name__)


class TaskProgressService:
    """
    Service for managing task progress records.
    """
    
    @staticmethod
    def create_task(task_id: str, entity: str, total: int) -> TaskProgress:
        """
        Create a new task progress record.
        """
        return TaskProgress.objects.create(
            task_id=task_id,
            entity=entity,
            status='queued',
            total=total,
            processed=0,
        )
    
    @staticmethod
    def get_task(task_id: str) -> Optional[TaskProgress]:
        """
        Get a task by ID.
        """
        try:
            return TaskProgress.objects.get(task_id=task_id)
        except TaskProgress.DoesNotExist:
            return None
    
    @staticmethod
    def update_status(task_id: str, status: str, error: Optional[str] = None) -> TaskProgress:
        """
        Update task status.
        """
        task = TaskProgressService.get_task(task_id)
        if not task:
            raise ValidationError({'task_id': f'Task {task_id} not found.'})
        
        task.status = status
        if error:
            task.error = error
        task.save(update_fields=['status', 'error', 'updated_at'])
        return task
    
    @staticmethod
    def update_progress(task_id: str, processed: int, current_entity: Optional[str] = None) -> TaskProgress:
        """
        Update processing progress.
        """
        task = TaskProgressService.get_task(task_id)
        if not task:
            raise ValidationError({'task_id': f'Task {task_id} not found.'})
        
        task.processed = processed
        if current_entity is not None:
            task.current_entity = current_entity
        task.save(update_fields=['processed', 'current_entity', 'updated_at'])
        return task
    
    @staticmethod
    def update_result(task_id: str, result: Dict[str, Any]) -> TaskProgress:
        """
        Update the result field (partial or final).
        """
        task = TaskProgressService.get_task(task_id)
        if not task:
            raise ValidationError({'task_id': f'Task {task_id} not found.'})
        
        task.result = result
        task.save(update_fields=['result', 'updated_at'])
        return task
    
    @staticmethod
    def mark_completed(task_id: str, result: Dict[str, Any]) -> TaskProgress:
        """
        Mark task as completed and store final result.
        """
        task = TaskProgressService.get_task(task_id)
        if not task:
            raise ValidationError({'task_id': f'Task {task_id} not found.'})
        
        task.status = 'completed'
        task.result = result
        task.current_entity = None
        task.processed = task.total  # assume all processed
        task.save(update_fields=['status', 'result', 'current_entity', 'processed', 'updated_at'])
        return task
    
    @staticmethod
    def mark_failed(task_id: str, error: str) -> TaskProgress:
        """
        Mark task as failed.
        """
        task = TaskProgressService.get_task(task_id)
        if not task:
            raise ValidationError({'task_id': f'Task {task_id} not found.'})
        
        task.status = 'failed'
        task.error = error
        task.current_entity = None
        task.save(update_fields=['status', 'error', 'current_entity', 'updated_at'])
        return task