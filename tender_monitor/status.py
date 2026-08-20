"""Shared, in-process state describing the most recent (or currently running) collection cycle.

Lives in its own module (rather than in scheduler.py or api.py) because both the scheduler, which
writes it, and the API handler, which reads it for GET /collection/status, need it -- either one
importing the other would be circular.
"""
last_cycle = {"phase":"not started yet","started_at":None,"finished_at":None,"duration_seconds":None,"counts":{}}
