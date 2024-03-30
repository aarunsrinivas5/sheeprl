from __future__ import annotations

import copy
from math import prod
from typing import Any, Dict, List, Optional, Sequence, Tuple

import gymnasium
import torch
import torch.nn as nn
from lightning import Fabric
from torch import Tensor
from torch.distributions import Distribution, Independent, Normal

from sheeprl.models.models import MLP, MultiEncoder, NatureCNN
from sheeprl.utils.distribution import OneHotCategoricalValidateArgs
from sheeprl.utils.fabric import get_single_device_fabric


class CNNEncoder(nn.Module):
    def __init__(
        self,
        in_channels: int,
        features_dim: int,
        screen_size: int,
        keys: Sequence[str],
    ) -> None:
        super().__init__()
        self.keys = keys
        self.input_dim = (in_channels, screen_size, screen_size)
        self.output_dim = features_dim
        self.model = NatureCNN(in_channels=in_channels, features_dim=features_dim, screen_size=screen_size)

    def forward(self, obs: Dict[str, Tensor]) -> Tensor:
        x = torch.cat([obs[k] for k in self.keys], dim=-3)
        return self.model(x)


class MLPEncoder(nn.Module):
    def __init__(
        self,
        input_dim: int,
        features_dim: int | None,
        keys: Sequence[str],
        dense_units: int = 64,
        mlp_layers: int = 2,
        dense_act: nn.Module = nn.ReLU,
        layer_norm: bool = False,
    ) -> None:
        super().__init__()
        self.keys = keys
        self.input_dim = input_dim
        self.output_dim = features_dim if features_dim else dense_units
        self.model = MLP(
            input_dim,
            features_dim,
            [dense_units] * mlp_layers,
            activation=dense_act,
            norm_layer=[nn.LayerNorm for _ in range(mlp_layers)] if layer_norm else None,
            norm_args=[{"normalized_shape": dense_units} for _ in range(mlp_layers)] if layer_norm else None,
        )

    def forward(self, obs: Dict[str, Tensor]) -> Tensor:
        x = torch.cat([obs[k] for k in self.keys], dim=-1)
        return self.model(x)


class PPOActor(nn.Module):
    def __init__(self, actor_backbone: torch.nn.Module, actor_heads: torch.nn.ModuleList, is_continuous: bool) -> None:
        super().__init__()
        self.actor_backbone = actor_backbone
        self.actor_heads = actor_heads
        self.is_continuous = is_continuous

    def forward(self, x: Tensor) -> List[Tensor]:
        x = self.actor_backbone(x)
        return [head(x) for head in self.actor_heads]


class PPOAgent(nn.Module):
    def __init__(
        self,
        actions_dim: Sequence[int],
        obs_space: gymnasium.spaces.Dict,
        encoder_cfg: Dict[str, Any],
        actor_cfg: Dict[str, Any],
        critic_cfg: Dict[str, Any],
        cnn_keys: Sequence[str],
        mlp_keys: Sequence[str],
        screen_size: int,
        distribution_cfg: Dict[str, Any],
        is_continuous: bool = False,
    ):
        super().__init__()
        self.is_continuous = is_continuous
        self.distribution_cfg = distribution_cfg
        self.actions_dim = actions_dim
        in_channels = sum([prod(obs_space[k].shape[:-2]) for k in cnn_keys])
        mlp_input_dim = sum([obs_space[k].shape[0] for k in mlp_keys])
        cnn_encoder = (
            CNNEncoder(in_channels, encoder_cfg.cnn_features_dim, screen_size, cnn_keys)
            if cnn_keys is not None and len(cnn_keys) > 0
            else None
        )
        mlp_encoder = (
            MLPEncoder(
                mlp_input_dim,
                encoder_cfg.mlp_features_dim,
                mlp_keys,
                encoder_cfg.dense_units,
                encoder_cfg.mlp_layers,
                eval(encoder_cfg.dense_act),
                encoder_cfg.layer_norm,
            )
            if mlp_keys is not None and len(mlp_keys) > 0
            else None
        )
        self.feature_extractor = MultiEncoder(cnn_encoder, mlp_encoder)
        features_dim = self.feature_extractor.output_dim
        self.critic = MLP(
            input_dims=features_dim,
            output_dim=1,
            hidden_sizes=[critic_cfg.dense_units] * critic_cfg.mlp_layers,
            activation=eval(critic_cfg.dense_act),
            norm_layer=[nn.LayerNorm for _ in range(critic_cfg.mlp_layers)] if critic_cfg.layer_norm else None,
            norm_args=(
                [{"normalized_shape": critic_cfg.dense_units} for _ in range(critic_cfg.mlp_layers)]
                if critic_cfg.layer_norm
                else None
            ),
        )
        actor_backbone = (
            MLP(
                input_dims=features_dim,
                output_dim=None,
                hidden_sizes=[actor_cfg.dense_units] * actor_cfg.mlp_layers,
                activation=eval(actor_cfg.dense_act),
                flatten_dim=None,
                norm_layer=[nn.LayerNorm] * actor_cfg.mlp_layers if actor_cfg.layer_norm else None,
                norm_args=(
                    [{"normalized_shape": actor_cfg.dense_units} for _ in range(actor_cfg.mlp_layers)]
                    if actor_cfg.layer_norm
                    else None
                ),
            )
            if actor_cfg.mlp_layers > 0
            else nn.Identity()
        )
        if is_continuous:
            actor_heads = nn.ModuleList([nn.Linear(actor_cfg.dense_units, sum(actions_dim) * 2)])
        else:
            actor_heads = nn.ModuleList([nn.Linear(actor_cfg.dense_units, action_dim) for action_dim in actions_dim])
        self.actor = PPOActor(actor_backbone, actor_heads, is_continuous)

    def forward(
        self, obs: Dict[str, Tensor], actions: Optional[List[Tensor]] = None, greedy: bool = False
    ) -> Tuple[Sequence[Tensor], Tensor, Tensor, Tensor]:
        feat = self.feature_extractor(obs)
        values = self.critic(feat)
        actor_out: List[Tensor] = self.actor(feat)
        if self.is_continuous:
            mean, log_std = torch.chunk(actor_out[0], chunks=2, dim=-1)
            std = log_std.exp()
            normal = Independent(
                Normal(mean, std, validate_args=self.distribution_cfg.validate_args),
                1,
                validate_args=self.distribution_cfg.validate_args,
            )
            if actions is None:
                if greedy:
                    actions = mean
                else:
                    actions = normal.sample()
            else:
                # always composed by a tuple of one element containing all the
                # continuous actions
                actions = actions[0]
            log_prob = normal.log_prob(actions)
            return tuple([actions]), log_prob.unsqueeze(dim=-1), normal.entropy().unsqueeze(dim=-1), values
        else:
            should_append = False
            actions_logprobs: List[Tensor] = []
            actions_entropies: List[Tensor] = []
            actions_dist: List[Distribution] = []
            if actions is None:
                should_append = True
                actions: List[Tensor] = []
            for i, logits in enumerate(actor_out):
                actions_dist.append(
                    OneHotCategoricalValidateArgs(logits=logits, validate_args=self.distribution_cfg.validate_args)
                )
                actions_entropies.append(actions_dist[-1].entropy())
                if should_append:
                    if greedy:
                        actions.append(actions_dist[-1].mode)
                    else:
                        actions.append(actions_dist[-1].sample())
                actions_logprobs.append(actions_dist[-1].log_prob(actions[i]))
            return (
                tuple(actions),
                torch.stack(actions_logprobs, dim=-1).sum(dim=-1, keepdim=True),
                torch.stack(actions_entropies, dim=-1).sum(dim=-1, keepdim=True),
                values,
            )

    def get_value(self, obs: Dict[str, Tensor]) -> Tensor:
        feat = self.feature_extractor(obs)
        return self.critic(feat)


def build_agent(
    fabric: Fabric,
    actions_dim: Sequence[int],
    is_continuous: bool,
    cfg: Dict[str, Any],
    obs_space: gymnasium.spaces.Dict,
    agent_state: Optional[Dict[str, Tensor]] = None,
) -> Tuple[PPOAgent, PPOAgent]:
    agent = PPOAgent(
        actions_dim=actions_dim,
        obs_space=obs_space,
        encoder_cfg=cfg.algo.encoder,
        actor_cfg=cfg.algo.actor,
        critic_cfg=cfg.algo.critic,
        cnn_keys=cfg.algo.cnn_keys.encoder,
        mlp_keys=cfg.algo.mlp_keys.encoder,
        screen_size=cfg.env.screen_size,
        distribution_cfg=cfg.distribution,
        is_continuous=is_continuous,
    )
    if agent_state:
        agent.load_state_dict(agent_state)
    player = copy.deepcopy(agent)

    # Setup training agent
    agent = fabric.setup_module(agent)

    # Setup player agent
    fabric_player = get_single_device_fabric(fabric)
    player = fabric_player.setup_module(player)

    # Tie weights between the agent and the player
    for agent_p, player_p in zip(agent.parameters(), player.parameters()):
        player_p.data = agent_p.data
    return agent, player
