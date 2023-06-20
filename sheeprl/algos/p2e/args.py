from dataclasses import dataclass

from sheeprl.algos.dreamer_v1.args import DreamerV1Args
from sheeprl.utils.parser import Arg


@dataclass
class P2EArgs(DreamerV1Args):
    # override
    stochastic_size: int = Arg(default=60, help="the dimension of the stochastic state")
    recurrent_state_size: int = Arg(default=400, help="the dimension of the recurrent state")

    # new args
    num_ensembles: int = Arg(default=5, help="the number of the ensembles to compute the intrinsic reward")
    ensemble_lr: float = Arg(default=1e-3, help="the learning rate of the optimizer of the ensembles")
    ensemble_eps: float = Arg(default=1e-4, help="the epsilon of the Adam optimizer of the ensembles")
    ensemble_clip_gradients: float = Arg(default=1000, help="how much to clip the gradient norms of the ensembles")
    intrinsic_reward_multiplier: float = Arg(default=10000, help="how much scale the intrinsic rewards")