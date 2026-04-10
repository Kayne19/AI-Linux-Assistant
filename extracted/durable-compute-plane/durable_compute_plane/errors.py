class ComputePlaneError(RuntimeError):
    pass


class ActiveScopeJobExistsError(ComputePlaneError):
    pass


class ActiveJobLimitExceededError(ComputePlaneError):
    pass


class JobNotFoundError(ComputePlaneError):
    pass


class JobRequeueError(ComputePlaneError):
    pass


class JobOwnershipLostError(ComputePlaneError):
    pass


class JobStateConflictError(ComputePlaneError):
    pass


class JobExecutionError(ComputePlaneError):
    pass


class JobCancelledError(ComputePlaneError):
    pass


class JobPausedError(ComputePlaneError):
    def __init__(self, message="Job paused.", checkpoint_state=None, payload=None):
        super().__init__(message)
        self.checkpoint_state = checkpoint_state or {}
        self.payload = payload or {}
