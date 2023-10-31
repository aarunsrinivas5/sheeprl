import datetime
import importlib
import os
import pathlib
import time
import warnings
from typing import Any, Dict

import hydra
from lightning import Fabric
from lightning.fabric.loggers.tensorboard import TensorBoardLogger
from lightning.fabric.strategies import STRATEGY_REGISTRY, DDPStrategy, SingleDeviceStrategy, Strategy
from omegaconf import DictConfig, OmegaConf

from sheeprl.utils.metric import MetricAggregator
from sheeprl.utils.registry import tasks
from sheeprl.utils.timer import timer
from sheeprl.utils.utils import dotdict, print_config


def resume_from_checkpoint(cfg: DictConfig) -> Dict[str, Any]:
    root_dir = cfg.root_dir
    run_name = cfg.run_name
    ckpt_path = pathlib.Path(cfg.checkpoint.resume_from)
    old_cfg = OmegaConf.load(ckpt_path.parent.parent.parent / ".hydra" / "config.yaml")
    if old_cfg.env.id != cfg.env.id:
        raise ValueError(
            "This experiment is run with a different environment from the one of the experiment you want to restart. "
            f"Got '{cfg.env.id}', but the environment of the experiment of the checkpoint was {old_cfg.env.id}. "
            "Set properly the environment for restarting the experiment."
        )
    if old_cfg.algo.name != cfg.algo.name:
        raise ValueError(
            "This experiment is run with a different algorithm from the one of the experiment you want to restart. "
            f"Got '{cfg.algo.name}', but the algorithm of the experiment of the checkpoint was {old_cfg.algo.name}. "
            "Set properly the algorithm name for restarting the experiment."
        )
    old_cfg.pop("root_dir", None)
    old_cfg.pop("run_name", None)
    cfg = dotdict(old_cfg)
    cfg.checkpoint.resume_from = str(ckpt_path)
    cfg.root_dir = root_dir
    cfg.run_name = run_name
    return cfg


def run_algorithm(cfg: Dict[str, Any]):
    """Run the algorithm specified in the configuration.

    Args:
        cfg (Dict[str, Any]): the loaded configuration.
    """
    # Given the algorithm's name, retrieve the module where
    # 'cfg.algo.name'.py is contained; from there retrieve the
    # 'register_algorithm'-decorated entrypoint;
    # the entrypoint will be launched by Fabric with 'fabric.launch(entrypoint)'
    module = None
    decoupled = False
    entrypoint = None
    algo_name = cfg.algo.name
    for _module, _algos in tasks.items():
        for _algo in _algos:
            if algo_name == _algo["name"]:
                module = _module
                entrypoint = _algo["entrypoint"]
                decoupled = _algo["decoupled"]
                break
    if module is None:
        raise RuntimeError(f"Given the algorithm named '{algo_name}', no module has been found to be imported.")
    if entrypoint is None:
        raise RuntimeError(
            f"Given the module and algorithm named '{module}' and '{algo_name}' respectively, "
            "no entrypoint has been found to be imported."
        )
    task = importlib.import_module(f"{module}.{algo_name}")
    utils = importlib.import_module(f"{module}.utils")
    command = task.__dict__[entrypoint]
    kwargs = {}
    if decoupled:
        root_dir = (
            os.path.join("logs", "runs", cfg.root_dir)
            if cfg.root_dir is not None
            else os.path.join("logs", "runs", algo_name, datetime.today().strftime("%Y-%m-%d_%H-%M-%S"))
        )
        run_name = (
            cfg.run_name if cfg.run_name is not None else f"{cfg.env.id}_{cfg.exp_name}_{cfg.seed}_{int(time.time())}"
        )
        logger = None
        if cfg.metric.log_level > 0:
            logger = TensorBoardLogger(root_dir=root_dir, name=run_name)
            logger.log_hyperparams(cfg)
        fabric: Fabric = hydra.utils.instantiate(cfg.fabric, _convert_="all")
        if logger is not None:
            fabric._loggers.extend([logger])
    else:
        strategy = cfg.fabric.pop("strategy", "auto")
        if "sac_ae" in module:
            if strategy is not None:
                warnings.warn(
                    "You are running the SAC-AE algorithm you have specified a strategy different than 'ddp': "
                    f"'python sheeprl.py fabric.strategy={strategy}'. This algorithm is run with the "
                    "'lightning.fabric.strategies.DDPStrategy' strategy."
                )
            strategy = DDPStrategy(find_unused_parameters=True)
        elif "finetuning" in module:
            # Load exploration configurations
            ckpt_path = pathlib.Path(cfg.checkpoint.exploration_ckpt_path)
            exploration_cfg = OmegaConf.load(ckpt_path.parent.parent.parent / ".hydra" / "config.yaml")
            exploration_cfg.pop("root_dir", None)
            exploration_cfg.pop("run_name", None)
            exploration_cfg = dotdict(OmegaConf.to_container(exploration_cfg, resolve=True, throw_on_missing=True))
            if exploration_cfg.env.id != cfg.env.id:
                raise ValueError(
                    "This experiment is run with a different environment from "
                    "the one of the exploration you want to finetune. "
                    f"Got '{cfg.env.id}', but the environment used during exploration was {exploration_cfg.env.id}. "
                    "Set properly the environment for finetuning the experiment."
                )
            kwargs["exploration_cfg"] = exploration_cfg
            cfg.fabric = exploration_cfg.fabric
        fabric: Fabric = hydra.utils.instantiate(cfg.fabric, strategy=strategy, _convert_="all")

    if hasattr(cfg, "metric") and cfg.metric is not None:
        predefined_metric_keys = set()
        if not hasattr(utils, "AGGREGATOR_KEYS"):
            warnings.warn(
                f"No 'AGGREGATOR_KEYS' set found for the {algo_name} algorithm under the {module} module. "
                "No metric will be logged.",
                UserWarning,
            )
        else:
            predefined_metric_keys = utils.AGGREGATOR_KEYS
        timer.disabled = cfg.metric.log_level == 0 or cfg.metric.disable_timer
        keys_to_remove = set(cfg.metric.aggregator.metrics.keys()) - predefined_metric_keys
        for k in keys_to_remove:
            cfg.metric.aggregator.metrics.pop(k, None)
        MetricAggregator.disabled = cfg.metric.log_level == 0 or len(cfg.metric.aggregator.metrics) == 0
    fabric.launch(command, cfg, **kwargs)


def check_configs(cfg: Dict[str, Any]):
    """Check the validity of the configuration.

    Args:
        cfg (Dict[str, Any]): the loaded configuration to check.
    """
    decoupled = False
    algo_name = cfg.algo.name
    for _, _algos in tasks.items():
        for _algo in _algos:
            if algo_name == _algo["name"]:
                decoupled = _algo["decoupled"]
                break
    strategy = cfg.fabric.strategy
    available_strategies = STRATEGY_REGISTRY.available_strategies()
    if decoupled:
        if isinstance(strategy, str):
            strategy = strategy.lower()
            if not (strategy in available_strategies and "ddp" in strategy):
                raise ValueError(
                    f"{strategy} is currently not supported for decoupled algorithm. "
                    "Please launch the script with a DDP strategy: "
                    "'python sheeprl.py fabric.strategy=ddp'"
                )
        elif (
            "_target_" in strategy
            and issubclass((strategy := hydra.utils.get_class(strategy._target_)), Strategy)
            and not issubclass(strategy, DDPStrategy)
        ):
            raise ValueError(
                f"{strategy.__qualname__} is currently not supported for decoupled algorithms. "
                "Please launch the script with a 'DDP' strategy with 'python sheeprl.py fabric.strategy=ddp'"
            )
    else:
        if isinstance(strategy, str):
            strategy = strategy.lower()
            if strategy != "auto" and not (strategy in available_strategies and "ddp" in strategy):
                warnings.warn(
                    f"Running an algorithm with a strategy ({strategy}) "
                    "different than 'auto' or 'dpp' can cause unexpected problems. "
                    "Please launch the script with a 'DDP' strategy with 'python sheeprl.py fabric.strategy=ddp' "
                    "or the 'auto' one with 'python sheeprl.py fabric.strategy=auto' if you run into any problems.",
                    UserWarning,
                )
        elif (
            "_target_" in strategy
            and issubclass((strategy := hydra.utils.get_class(strategy._target_)), Strategy)
            and not issubclass(strategy, (DDPStrategy, SingleDeviceStrategy))
        ):
            warnings.warn(
                f"Running an algorithm with a strategy ({strategy.__qualname__}) "
                "different than 'SingleDeviceStrategy' or 'DDPStrategy' can cause unexpected problems. "
                "Please launch the script with a 'DDP' strategy with 'python sheeprl.py fabric.strategy=ddp' "
                "or with a single device with 'python sheeprl.py fabric.strategy=auto fabric.devices=1' "
                "if you run into any problems.",
                UserWarning,
            )


@hydra.main(version_base="1.13", config_path="configs", config_name="config")
def run(cfg: DictConfig):
    """SheepRL zero-code command line utility."""
    if cfg.metric.log_level > 0:
        print_config(cfg)
    cfg = dotdict(OmegaConf.to_container(cfg, resolve=True, throw_on_missing=True))
    if cfg.checkpoint.resume_from:
        cfg = resume_from_checkpoint(cfg)
    check_configs(cfg)
    run_algorithm(cfg)
