POST /events slow (~1.5s) with all pods Ready — suspect api image regression
Reported submit-event slowness/timeouts; all pods Running/Ready, 0 restarts, no Warning events.
Both api replicas logged POST /events 202 at elapsed_ms ~1510–1530 (normal few ms); worker ~301 ms/event and Redis RTT ~1 ms, redis-0 healthy.
Cause: Deployment api image rollout changed <1 min before latency; handler waited for event processing. Rollback to previous image restored few-ms latency.
First checks: kubectl rollout history deployment/api and image change time; compare api pod logs elapsed_ms across replicas; if both slow and worker/Redis normal, roll back api image.
