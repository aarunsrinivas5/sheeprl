import typing
from typing import Union

import torch
from tensordict import TensorDict
from tensordict.tensordict import TensorDictBase
from torch import Size, Tensor, device


class ReplayBuffer:
    def __init__(self, buffer_size: int, n_envs: int = 1, device: Union[device, str] = "cpu"):
        """Replay buffer used in off-policy algorithms like SAC/TD3.
        The replay buffer internally uses a TensorDict

        Args:
            buffer_size (int): The buffer size.
            n_envs (int, optional): The number of environments. Defaults to 1.
            device (Union[torch.device, str], optional): The device where the buffer is created. Defaults to "cpu".
        """
        self._buffer_size = buffer_size
        self._n_envs = n_envs
        if isinstance(device, str):
            device = torch.device(device=device)
        self._device = device
        self._buf = TensorDict({}, batch_size=[buffer_size, n_envs], device=device)
        self._pos = 0
        self._full = False

    @property
    def buffer(self) -> TensorDictBase:
        return self._buf

    @property
    def buffer_size(self) -> int:
        return self._buffer_size

    @property
    def full(self) -> int:
        return self._full

    @property
    def n_envs(self) -> int:
        return self._n_envs

    @property
    def shape(self) -> Size:
        return self.buffer.shape

    @property
    def device(self) -> device:
        return self._device

    def __len__(self) -> int:
        return self.buffer_size

    @typing.overload
    def add(self, data: "ReplayBuffer") -> None:
        ...

    @typing.overload
    def add(self, data: TensorDictBase) -> None:
        ...

    def add(self, data: Union["ReplayBuffer", TensorDictBase]) -> None:
        """Add data to the buffer.

        Args:
            data: data to add.

        Raises:
            RuntimeError: the number of dimensions (the batch_size of the TensorDictBase) must be 2:
            one for the number of environments and one for the sequence length.
        """
        if isinstance(data, ReplayBuffer):
            data = data.buffer
        elif not isinstance(data, TensorDictBase):
            raise TypeError("`data` must be a TensorDictBase or a fabricrl.data.ReplayBuffer")
        if len(data.shape) != 2:
            raise RuntimeError(
                "`data` must have 2 batch dimensions: [sequence_length, n_envs, d1, ..., dn]. "
                "`sequence_length` and `n_envs` should be 1. Shape is: {}".format(data.shape)
            )
        data_len = data.shape[0]
        next_pos = (self._pos + data_len) % self._buffer_size
        if self._pos == 0 and next_pos == 0:
            next_pos = data_len
        if next_pos < self._pos:
            idxes = torch.tensor(
                list(range(self._pos, self._buffer_size)) + list(range(0, next_pos)), device=self.device
            )
        else:
            idxes = torch.tensor(range(self._pos, next_pos), device=self.device)
        self._buf[idxes, :] = data
        if self._pos + data_len >= self._buffer_size:
            self._full = True
        self._pos = next_pos

    def sample(self, batch_size: int) -> TensorDictBase:
        """Sample elements from the replay buffer.

        Custom sampling when using memory efficient variant,
        as we should not sample the element with index `self.pos`
        See https://github.com/DLR-RM/stable-baselines3/pull/28#issuecomment-637559274

        Args:
            batch_size (int): batch_size (int): Number of element to sample

        Returns:
            TensorDictBase: the sampled TensorDictBase, cloned
        """
        if batch_size <= 0:
            raise ValueError("Batch size must be greater than 0")
        if batch_size > self._buf.shape[0]:
            raise ValueError(f"Batch size {batch_size} is larger than the replay buffer size {self._buf.shape[0]}")
        # Do not sample the element with index `self.pos` as the transitions is invalid
        # (we use only one array to store `obs` and `next_obs`)
        if self._full:
            batch_idxes = (
                torch.randint(1, self._buffer_size, size=(batch_size, self.n_envs), device=self.device) + self._pos
            ) % self._buffer_size
        else:
            batch_idxes = torch.randint(0, self._pos, size=(batch_size, self.n_envs), device=self.device)
        return self._get_samples(batch_idxes)

    def _get_samples(self, batch_idxes: Tensor) -> TensorDictBase:
        buf: TensorDictBase = torch.gather(self._buf, dim=0, index=batch_idxes).clone()
        buf["next_obs"] = self._buf["observations"][
            (batch_idxes + 1) % self._buffer_size, torch.arange(self.n_envs, device=self.device)
        ].clone()
        return buf

    def __getitem__(self, key: str) -> torch.Tensor:
        if not isinstance(key, str):
            raise TypeError("`key` must be a string")
        return self._buf.get(key)

    def __setitem__(self, key: str, t: Tensor) -> None:
        self.buffer.set(key, t)
