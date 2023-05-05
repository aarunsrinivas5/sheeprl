import torch
from gymnasium.vector import SyncVectorEnv
from lightning.fabric.loggers import Logger

from fabricrl.algos.ppo.args import PPOArgs
from fabricrl.algos.ppo.utils import make_env
from fabricrl.algos.ppo_recurrent.agent import RecurrentPPOAgent


@torch.inference_mode()
def test(agent: RecurrentPPOAgent, device: torch.device, logger: Logger, args: PPOArgs):
    env = SyncVectorEnv(
        [make_env(args.env_id, args.seed, 0, args.capture_video, logger.log_dir, "test", mask_velocities=args.mask_vel)]
    )
    done = False
    cumulative_rew = 0
    next_obs = torch.tensor(env.reset(seed=args.seed)[0], device=device).unsqueeze(0)
    state = agent.initial_states[0]
    while not done:
        # Act greedly through the environment
        action, state = agent.get_greedy_action(next_obs, state)

        # Single environment step
        next_obs, reward, done, truncated, info = env.step(action.cpu().numpy().reshape(env.action_space.shape))
        done = done or truncated
        cumulative_rew += reward
        next_obs = torch.tensor(next_obs, device=device).unsqueeze(0)
    logger.log_metrics({"Test/cumulative_reward": cumulative_rew}, 0)
    env.close()
