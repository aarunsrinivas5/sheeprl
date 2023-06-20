"""Adapted from https://github.com/denisyarats/dmc2gym/blob/master/dmc2gym/wrappers.py"""

from typing import Any, Dict, Optional, Tuple

import gymnasium as gym
import minedojo
import numpy as np
from gymnasium import core

ACTION_MAP = {
    0: np.array([0, 0, 0, 12, 12, 0, 0, 0]),
    1: np.array([1, 0, 0, 12, 12, 0, 0, 0]),
    2: np.array([2, 0, 0, 12, 12, 0, 0, 0]),
    3: np.array([0, 1, 0, 12, 12, 0, 0, 0]),
    4: np.array([0, 2, 0, 12, 12, 0, 0, 0]),
    5: np.array([1, 0, 1, 12, 12, 0, 0, 0]),
    6: np.array([1, 0, 2, 12, 12, 0, 0, 0]),
    7: np.array([1, 0, 3, 12, 12, 0, 0, 0]),
    8: np.array([0, 0, 0, 11, 12, 0, 0, 0]),
    9: np.array([0, 0, 0, 13, 12, 0, 0, 0]),
    10: np.array([0, 0, 0, 12, 11, 0, 0, 0]),
    11: np.array([0, 0, 0, 12, 13, 0, 0, 0]),
    12: np.array([0, 0, 0, 12, 12, 3, 0, 0]),
}


class MineDojoWrapper(core.Env):
    def __init__(
        self,
        task_id: str,
        height: int = 64,
        width: int = 64,
        pitch_limits: Tuple[int, int] = (-60, 60),
        seed: Optional[int] = None,
        **kwargs: Optional[Dict[Any, Any]],
    ):
        self._height = height
        self._width = width
        self._pitch_limits = pitch_limits
        self._pos = kwargs.pop("start_position", None)
        self._action_space = gym.spaces.Discrete(len(ACTION_MAP.keys()))

        if not (self._pitch_limits[0] <= self._pos["pitch"] <= self._pitch_limits[1]):
            raise ValueError(
                f"The initial position must respect the pitch limits {self._pitch_limits}, given {self._pos['pitch']}"
            )

        # create task
        self._env = minedojo.make(
            task_id=task_id,
            image_size=(height, width),
            world_seed=seed,
            start_position=self._pos,
            **kwargs,
        )
        # render
        self._render_mode: str = "rgb_array"
        # set seed
        self.seed(seed=seed)

    def __getattr__(self, name):
        return getattr(self._env, name)

    def _convert_action(self, action) -> np.ndarray:
        return ACTION_MAP[action.argmax().item()]

    @property
    def render_mode(self) -> str:
        return self._render_mode

    @property
    def action_space(self) -> gym.spaces.Space:
        return self._action_space

    @property
    def observation_space(self) -> gym.spaces.Space:
        return self._env.observation_space["rgb"]

    def seed(self, seed: Optional[int] = None) -> None:
        self.observation_space.seed(seed)
        self.action_space.seed(seed)

    def step(self, action) -> Dict[str, Any]:
        action = self._convert_action(action)
        next_pitch = self._pos["pitch"] + (action[3] - 12) * 15
        if not (self._pitch_limits[0] <= next_pitch <= self._pitch_limits[1]):
            action[3] = 12

        obs, reward, done, info = self._env.step(action)
        self._pos = {
            "x": obs["location_stats"]["pos"][0],
            "y": obs["location_stats"]["pos"][1],
            "z": obs["location_stats"]["pos"][2],
            "pitch": obs["location_stats"]["pitch"].item(),
            "yaw": obs["location_stats"]["yaw"].item(),
        }
        return obs, reward, done, False, info

    def reset(
        self, seed: Optional[int] = None, options: Optional[Dict[str, Any]] = None
    ) -> Tuple[np.ndarray, Dict[str, Any]]:
        obs = self._env.reset()
        self._pos = {
            "x": obs["location_stats"]["pos"][0],
            "y": obs["location_stats"]["pos"][1],
            "z": obs["location_stats"]["pos"][2],
            "pitch": obs["location_stats"]["pitch"].item(),
            "yaw": obs["location_stats"]["yaw"].item(),
        }
        return obs, {}

    def close(self):
        self._env.close()
        return super().close()