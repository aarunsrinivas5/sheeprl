from typing import Tuple

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch import Tensor

from fabricrl.models.models import MLP


class RecurrentPPOAgent(nn.Module):
    def __init__(self, observation_dim: int, action_dim: int, num_envs: int = 1):
        super().__init__()
        # Actor: Obs -> Feature -> LSTM -> Logits
        self._actor_fc = MLP(
            input_dims=observation_dim,
            output_dim=0,
            hidden_sizes=(64, 64),
            activation=nn.ReLU,
            flatten_dim=2,
        )
        self._actor_rnn = nn.LSTM(
            input_size=self._actor_fc.output_dim, hidden_size=self._actor_fc.output_dim, batch_first=False
        )
        self._actor_logits = MLP(self._actor_fc.output_dim, action_dim, flatten_dim=None)

        # Critic: Obs -> Feature -> LSTM -> Values
        self._critic_fc = MLP(
            input_dims=observation_dim,
            output_dim=0,
            hidden_sizes=(64, 64),
            activation=nn.ReLU,
            flatten_dim=2,
        )
        self._critic_rnn = nn.LSTM(
            input_size=self._critic_fc.output_dim, hidden_size=self._critic_fc.output_dim, batch_first=False
        )
        self._critic = MLP(self._critic_fc.output_dim, 1, flatten_dim=None)

        # Initial recurrent states for both the actor and critic rnn
        self._initial_states: Tuple[Tuple[Tensor, Tensor], Tuple[Tensor, Tensor]] = (
            (
                torch.zeros(1, num_envs, self._actor_fc.output_dim),
                torch.zeros(1, num_envs, self._actor_fc.output_dim),
            ),
            (
                torch.zeros(1, num_envs, self._critic_fc.output_dim),
                torch.zeros(1, num_envs, self._critic_fc.output_dim),
            ),
        )

    @property
    def initial_states(self) -> Tuple[Tuple[Tensor, Tensor], Tuple[Tensor, Tensor]]:
        return self._initial_states

    @initial_states.setter
    def initial_states(self, value: Tuple[Tuple[Tensor, Tensor], Tuple[Tensor, Tensor]]) -> None:
        self._initial_states = value

    def get_greedy_action(self, obs: Tensor, state: Tuple[Tensor, Tensor]) -> Tuple[Tensor, Tuple[Tensor, Tensor]]:
        x = self._actor_fc(obs)
        x, state = self._actor_rnn(x, state)
        logits = self._actor_logits(x)
        probs = F.softmax(logits, dim=-1)
        return torch.argmax(probs, dim=-1), state

    def get_logits(
        self, obs: Tensor, dones: Tensor, actor_state: Tuple[Tensor, Tensor]
    ) -> Tuple[Tensor, Tuple[Tensor, Tensor]]:
        # If no done is found, then we can run through all the sequence.
        # https://github.com/Stable-Baselines-Team/stable-baselines3-contrib/blob/master/sb3_contrib/common/recurrent/policies.py#L22
        run_through_all = False
        if torch.all(dones == 0.0):
            run_through_all = True

        x_actor = self._actor_fc(obs)
        self._actor_rnn.flatten_parameters()
        if run_through_all:
            actor_hidden, actor_state = self._actor_rnn(x_actor, actor_state)
        else:
            actor_hidden = torch.empty_like(x_actor)
            for i, (ah, d) in enumerate(zip(x_actor, dones)):
                ah, actor_state = self._actor_rnn(
                    ah.unsqueeze(0), tuple([(1.0 - d).view(1, -1, 1) * s for s in actor_state])
                )
                actor_hidden[i] = ah
        logits = self._actor_logits(actor_hidden)
        return logits, actor_state

    def get_values(
        self, obs: Tensor, dones: Tensor, critic_state: Tuple[Tensor, Tensor]
    ) -> Tuple[Tensor, Tuple[Tensor, Tensor]]:
        # If no done is found, then we can run through all the sequence.
        # https://github.com/Stable-Baselines-Team/stable-baselines3-contrib/blob/master/sb3_contrib/common/recurrent/policies.py#L22
        run_through_all = False
        if torch.all(dones == 0.0):
            run_through_all = True

        x_critic = self._critic_fc(obs)
        self._critic_rnn.flatten_parameters()
        if run_through_all:
            critic_hidden, critic_state = self._critic_rnn(x_critic, critic_state)
        else:
            critic_hidden = torch.empty_like(x_critic)
            for i, (ch, d) in enumerate(zip(x_critic, dones)):
                ch, critic_state = self._critic_rnn(
                    ch.unsqueeze(0), tuple([(1.0 - d).view(1, -1, 1) * s for s in critic_state])
                )
                critic_hidden[i] = ch
        values = self._critic(critic_hidden)
        return values, critic_state

    def forward(
        self,
        obs: Tensor,
        dones: Tensor,
        state: Tuple[Tuple[Tensor, Tensor], Tuple[Tensor, Tensor]] = ((None, None), (None, None)),
    ) -> Tuple[Tensor, Tensor, Tuple[Tuple[Tensor, Tensor], Tuple[Tensor, Tensor]]]:
        """Compute actor logits and critic values.

        Args:
            obs (Tensor): observations collected
            dones (Tensor): dones flag collected
            state (Tuple[Tuple[Tensor, Tensor], Tuple[Tensor, Tensor]], optional): the recurrent states.
                Defaults to None.

        Returns:
            actor logits
            critic values
            next recurrent state for both the actor and the critic
        """
        actor_state, critic_state = state
        logits, actor_state = self.get_logits(obs, dones, tuple([s.to(obs.device) for s in actor_state]))
        values, critic_state = self.get_values(obs, dones, tuple([s.to(obs.device) for s in critic_state]))
        return logits, values, (actor_state, critic_state)
