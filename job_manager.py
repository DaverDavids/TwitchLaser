import json
import os
import uuid
from datetime import datetime
from config import debug_print

class JobManager:
    def __init__(self, data_dir='data/jobs'):
        self.data_dir = data_dir
        self.jobs_file = os.path.join(data_dir, 'jobs.json')
        self.gcode_dir = os.path.join(data_dir, 'gcode')
        self.jobs = []
        
        os.makedirs(self.gcode_dir, exist_ok=True)
        self.load()

    def load(self):
        if os.path.exists(self.jobs_file):
            try:
                with open(self.jobs_file, 'r') as f:
                    self.jobs = json.load(f)
            except Exception as e:
                debug_print(f"Failed to load jobs: {e}")
                self.jobs = []
        
        # Reset any stuck 'active' jobs to 'stopped' on startup
        for job in self.jobs:
            if job['status'] == 'active':
                job['status'] = 'stopped'
                job['error'] = 'Interrupted by server restart'
        self.save()

    def save(self):
        try:
            with open(self.jobs_file, 'w') as f:
                json.dump(self.jobs, f, indent=2)
        except Exception as e:
            debug_print(f"Failed to save jobs: {e}")

    def add_job(self, name, source='twitch', settings=None):
        job = {
            'id': str(uuid.uuid4())[:8],
            'name': name,
            'source': source,
            'status': 'pending', # pending, active, finished, failed, stopped
            'timestamp': datetime.now().isoformat(),
            'completed_time': None,
            'error': None,
            'settings': settings or {},
            'gcode_file': None
        }
        self.jobs.insert(0, job) # Newest at top
        self.save()
        return job

    def update_job(self, job_id, **kwargs):
        for job in self.jobs:
            if job['id'] == job_id:
                job.update(kwargs)
                if 'status' in kwargs and kwargs['status'] in ('finished', 'failed', 'stopped'):
                    job['completed_time'] = datetime.now().isoformat()
                self.save()
                return job
        return None

    def get_next_pending(self):
        # Since newest is at top (index 0), we search from bottom to process oldest first
        for job in reversed(self.jobs):
            if job['status'] == 'pending':
                return job
        return None
        
    def get_jobs(self):
        return self.jobs

    def get_job(self, job_id):
        for job in self.jobs:
            if job['id'] == job_id:
                return job
        return None

    def save_gcode(self, job_id, gcode_text):
        filename = f"{job_id}.gcode"
        path = os.path.join(self.gcode_dir, filename)
        try:
            with open(path, 'w') as f:
                f.write(gcode_text)
            self.update_job(job_id, gcode_file=filename)
        except Exception as e:
            debug_print(f"Failed to save GCode for {job_id}: {e}")

    def get_gcode_path(self, job_id):
        job = self.get_job(job_id)
        if job and job.get('gcode_file'):
            path = os.path.join(self.gcode_dir, job['gcode_file'])
            if os.path.exists(path):
                return path
        return None

    def redo_job(self, job_id):
        old_job = self.get_job(job_id)
        if not old_job: return None
        
        # Create a new job based on the old one
        new_job = self.add_job(old_job['name'], source=old_job['source'] + ' (Redo)')
        new_job['settings'] = dict(old_job.get('settings', {}))
        
        # If it had exact gcode, link the same file
        if old_job.get('gcode_file'):
            old_path = self.get_gcode_path(job_id)
            if old_path:
                with open(old_path, 'r') as f:
                    gcode = f.read()
                self.save_gcode(new_job['id'], gcode)
                
        self.save()
        return new_job
