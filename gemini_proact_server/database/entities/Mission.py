import logging
import json
from enum import Enum
from datetime import datetime
import math
from datetime import datetime

import attrs
from attrs import define, field, NOTHING
from typing import *
from typing_extensions import override

from .DatabaseEntity import DatabaseEntity


logger = logging.getLogger('proact.database.mission')


class DefaultValues():
    WEEKLY_MISSION_REGENERATION_MAX = 3
    ONGOING_PROJECT_REGENERATION_MAX = math.inf


class MissionStatus(Enum):
    NOT_STARTED = 'not started'
    IN_PROGRESS = 'in progress'
    DONE = 'done'
    EXPIRED = 'expired'


class MissionPeriodType(Enum): # Only needed for projects
    WEEKLY = 'weekly'
    '''Mission estimated to complete in 1 week.
    '''
    ONGOING = 'ongoing'
    '''Mission does not have a clear duration; deadline is flexible.
    '''

class MissionLevel(Enum):
    PROJECT = 'project'
    MISSION = 'mission'
    STEP = 'step'


@define(kw_only=True)
class BaseMission(DatabaseEntity):
    '''
    Represents an object in the `Mission` collection that can either be a Project, Mission, or Step
    '''
    title: str = field(default=NOTHING, repr=True)
    level: Type[MissionLevel] = field(default=NOTHING, repr=True) # will be defined by child classes  
    type: Type[MissionPeriodType] = field(default=None  )
    description: str = field(default="", repr=False)
    steps: List['BaseMission'] = field(factory=list, repr=False)
    status: Type[MissionStatus] = field(default=MissionStatus.NOT_STARTED, repr=False)
    deadline: Union[str, None] = field(default=None, repr=False)
    styleId: Union[str, None] = field(default=None, repr=False)
    ecoPoints: int = field(default=0, repr=False)
    CO2InKg: int = field(default=0, repr=False) 
    eventId: Union[str, None] = field(default=None, repr=False)
    regenerationLeft: int = field(default=-1, repr=False)
    createdTimestamp: Union[datetime, None] = field(factory=datetime.now, repr=False)

    @classmethod
    def from_dict(cls, data: dict) -> 'BaseMission':
        return cls(
            **data
        )

    def __attrs_post_init__(self):
        # convert some attribs from 'str' to 'Enum'
        if hasattr(self, 'type') and isinstance(self.type, str):
            self.type = MissionPeriodType._value2member_map_[self.type]
        if isinstance(self.level, str):
            self.level = MissionLevel._value2member_map_[self.level]

    
    def add_step(self, step:'BaseMission'):
        '''
        Add a `BaseMission` child to the current `BaseMission` object
        '''
        if not isinstance(step, BaseMission):
            msg = "Step must be an instance of `BaseMission`"
            logger.error(msg)
            raise ValueError("Step must be an instance of `BaseMission`")
        # append new step
        self.steps.append(step)
        # update stats
        self.ecoPoints += step.ecoPoints
        self.CO2InKg += step.CO2InKg


    def add_steps(self, steps:List['BaseMission']):
        for step in steps:
            self.add_step(step)

    
    def to_dict(self):
        d = attrs.asdict(self)
        d.update({
            'steps': [s.id for s in self.steps],
            'status': self.status.value,
            'type': self.type.value if hasattr(self, 'type') and self.type is not None else None,
            'level': self.level.value
        })
        del d['id'] # don't include id since it will be doc id
        return d



@define(kw_only=True)
class WeeklyProject(BaseMission):
    '''
    A Weekly Project that consists of missions. For now not much different than BaseMission.
    '''
    type: Type[MissionPeriodType] = field(default=MissionPeriodType.WEEKLY, repr=False)
    level: Type[MissionLevel] = field(default=MissionLevel.PROJECT, repr=False)
    regenerationLeft: int = field(default=-1) # weekly project can't be regenerated


@define(kw_only=True)
class OngoingProject(BaseException):
    '''
    An Ongoing Project that consists of ongoing missions. For now not much different than BaseMission.
    '''
    type: Type[MissionPeriodType] = field(default=MissionPeriodType.ONGOING, repr=False) 
    level: Type[MissionLevel] = field(default=MissionLevel.PROJECT, repr=False)
    regenerationLeft:int = field(default=DefaultValues.ONGOING_PROJECT_REGENERATION_MAX)


@define(kw_only=True)
class WeeklyMission(BaseMission):
    '''
    A Mission that consists of steps. For now no different than BaseMission.
    '''
    type: Type[MissionPeriodType] = field(default=MissionPeriodType.WEEKLY, repr=False)
    level: Type[MissionLevel] = field(default=MissionLevel.MISSION, repr=False)
    regenerationLeft:int = field(default=DefaultValues.WEEKLY_MISSION_REGENERATION_MAX)


@define(kw_only=True)
class OngoingMission(BaseMission):
    '''
    A Mission that consists of steps. For now no different than BaseMission.
    '''
    type: Type[MissionPeriodType] = field(default=MissionPeriodType.ONGOING, repr=False)
    level: Type[MissionLevel] = field(default=MissionLevel.MISSION, repr=False)
    regenerationLeft:int = field(default=-1) # Ongoing missions can't be regenerated


@define(kw_only=True)
class Step(BaseMission):
    '''
    A Step that consists of substeps. For now no different than BaseMission.
    '''
    level: Type[MissionLevel] = field(default=MissionLevel.STEP, repr=False)
    regenerationLeft:int = field(default=-1) # steps can't be regenerated


def create_mission_entity_from_dict(d: Dict) -> BaseMission:
    mission_entity_class:BaseMission

    # Project
    if d['level'] == MissionLevel.PROJECT.value:
        if d['type'] == MissionPeriodType.WEEKLY.value:
            mission_entity_class = WeeklyProject
        elif d['type'] == MissionPeriodType.ONGOING.value:
            mission_entity_class = OngoingProject
        else:
            raise ValueError(f"'type' is expected to be in {[t.value for t in MissionPeriodType]}. Got {d['type']} instead")
    # Mission
    elif d['level'] == MissionLevel.MISSION.value:
        if d['type'] == MissionPeriodType.WEEKLY.value:
            mission_entity_class = WeeklyMission
        elif d['type'] == MissionPeriodType.ONGOING.value:
            mission_entity_class = OngoingMission
        else:
            raise ValueError(f"'type' is expected to be in {[t.value for t in MissionPeriodType]}. Got {d['type']} instead")
    # Step
    elif d['level'] == MissionLevel.STEP.value:
        mission_entity_class = Step
    else:
        raise ValueError(ValueError(f"'level' is expected to be in {[t.value for t in MissionLevel]}. Got {d['level']} instead"))

    return mission_entity_class(**d)


# test driver
if __name__ == "__main__":
    project = WeeklyProject(
        title="Week 3"
    )

    m1 = WeeklyMission(
        title="Mission 1",
        description="Mission 1 description",
        ecoPoints=10,
        CO2InKg=20
    )
    m2 = WeeklyMission(
        title="Mission 2",
        description="Mission 2 description",
        ecoPoints=5,
        CO2InKg=15
    )

    project.add_steps([m1, m2])