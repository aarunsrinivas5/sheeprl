""" Adapted from https://huggingface.co/spaces/stabilityai/stablelm-tuned-alpha-chat/blob/main/app.py """
from dataclasses import asdict, dataclass, field
import json
from typing import Optional
import gradio as gr
import torch
from transformers import (
    AutoTokenizer,
    GenerationConfig,
    TextIteratorStreamer,
)
import time
import numpy as np
from torch.nn import functional as F
import os
from threading import Thread

from sheeprl.algos.rlhf.args import GenerationArgs, ModelArgs, TextDataArgs, TrainArgs
from sheeprl.algos.rlhf.models import CasualModel
from sheeprl.algos.rlhf.utils import load_args_from_json


@dataclass
class SummaryUI:
    max_size: int = field(default=1)
    concurrency_count: int = field(default=1)
    gen_args: Optional[GenerationArgs] = field(default=GenerationArgs(), init=False)
    args: Optional[dict] = field(default=None, init=False)
    model: Optional[CasualModel] = field(default=None, init=False)
    tokenizer: Optional[AutoTokenizer] = field(default=None, init=False)
    example_post: Optional[str] = field(default=None, init=False)
    checkpoint_names: Optional[list] = field(default=None, init=False)

    def load_model(self, experiment_dir: str, checkpoint_path: str):
        try:
            self.model_args = ModelArgs(**self.args["model_args"])
            self.text_args = TextDataArgs(**self.args["data_args"])
            model_path = (
                None if checkpoint_path == "pretrained" else os.path.join(experiment_dir, "model", checkpoint_path)
            )
            self.model = CasualModel.from_checkpoint(device="cuda", model_args=self.model_args, path=model_path)
            self.model = self.model.to("cuda")
            self.model.eval()

            tokenizer = AutoTokenizer.from_pretrained(self.model_args.model_name)
            if tokenizer.pad_token is None:
                tokenizer.pad_token = tokenizer.eos_token
                tokenizer.pad_token_id = tokenizer.eos_token_id
            self.tokenizer = tokenizer

            return gr.update(
                visible=True, value="""<h3 style="color:green;text-align:center">Model loaded successfully</h3>"""
            )
        except Exception as e:
            print(f"Model loading failed: {str(e)}")
            return gr.update(
                visible=True,
                value="""<h3 style="color:red;text-align:center;word-break: break-all;">Model load failed:{}{}</h3>""".format(
                    " ", str(e)
                ),
            )

    def load_experiment(self, experiment_dir: str):
        try:
            self.args = load_args_from_json(experiment_dir=experiment_dir)
            self.model_args = ModelArgs(**self.args["model_args"])
            self.text_args = TextDataArgs(**self.args["data_args"])
            self.gen_args = GenerationArgs(**self.args["gen_args"])
            checkpoint_names = ["pretrained"] + os.listdir(os.path.join(experiment_dir, "model"))
            info = gr.update(
                visible=True, value="""<h3 style="color:green;text-align:center">Experiment loaded successfully</h3>"""
            )
            dropdown = gr.update(choices=checkpoint_names)
            return info, dropdown

        except Exception as e:
            print(f"Experiment loading failed: {str(e)}")
            return gr.update(
                visible=True,
                value="""<h3 style="color:red;text-align:center;word-break: break-all;">Experiment load failed:{}{}</h3>""".format(
                    " ", str(e)
                ),
            )

    def load_example(self):
        print("Loading example")
        if hasattr(self, "text_args") and self.text_args is not None:
            data_path = self.text_args.destination_dir
            example_data = torch.load(os.path.join(data_path, "example_prompt.pt"))
            prompt = example_data["prompt"][:-7]
            subreddit_index = prompt.index("SUBREDDIT: ")
            title_index = prompt.index("TITLE: ")
            post_index = prompt.index("POST: ")
            subreddit = prompt[subreddit_index + 11 : title_index - 1]
            title = prompt[title_index + 7 : post_index - 1]
            post = prompt[post_index + 6 : -1]
            info = gr.update(
                visible=True, value="""<h3 style="color:green;text-align:center">Example loaded successfully</h3>"""
            )

            return info, gr.update(value=subreddit), gr.update(value=title), gr.update(value=post)
        else:
            return (
                gr.update(
                    visible=True,
                    value="""<h3 style="color:red;text-align:center;word-break: break-all;">First experiment</h3>""",
                ),
                None,
                None,
                None,
            )

    def clear(*args):
        return [None for _ in args]

    @torch.inference_mode()
    def summary(self, subreddit: str, title: str, post: str):
        prompt = f"SUBREDDIT: {subreddit} TITLE: {title} POST: {post} TL;DR: "

        # Tokenize the messages string
        model_inputs = self.tokenizer([prompt], return_tensors="pt").to("cuda")
        streamer = TextIteratorStreamer(self.tokenizer, timeout=10.0, skip_prompt=True, skip_special_tokens=True)
        generation_config = GenerationConfig.from_pretrained(self.model_args.model_name, **asdict(self.gen_args))
        t = Thread(
            target=self.model.generate,
            kwargs={**model_inputs, **{"generation_config": generation_config, "streamer": streamer}},
        )
        t.start()

        partial_text = ""
        for new_text in streamer:
            partial_text += new_text
        return partial_text

    def launch(self):
        with gr.Blocks() as demo:
            gr.Markdown("## LLM Summarization Demo")
            with gr.Row():
                with gr.Column(scale=5):
                    with gr.Row():
                        with gr.Column(scale=5):
                            exp_dir_input = gr.Textbox(
                                value="",
                                interactive=True,
                                placeholder="Enter the experiment directory",
                                label="Experiment Directory",
                                visible=True,
                            )
                            load_experiment_btn = gr.Button("Load Experiment")
                            load_experiment_info = gr.Markdown(visible=False, value="")
                            checkpoints = gr.Dropdown(
                                options=[],
                                label="Checkpoint",
                                visible=True,
                            )
                            load_model_btn = gr.Button("Load Model")
                            load_model_info = gr.Markdown(visible=False, value="")

                            load_experiment_btn.click(
                                self.load_experiment,
                                inputs=[exp_dir_input],
                                outputs=[load_experiment_info, checkpoints],
                                show_progress=True,
                            )
                            load_model_btn.click(
                                self.load_model,
                                inputs=[exp_dir_input, checkpoints],
                                outputs=[load_model_info],
                                show_progress=True,
                            )
                    with gr.Row():
                        with gr.Column(scale=3):
                            self.subreddit_text = gr.Textbox(
                                value="",
                                interactive=True,
                                placeholder="r/TechSupport",
                                label="Subreddit Name",
                                visible=True,
                            )
                            self.title_text = gr.Textbox(
                                value="",
                                interactive=True,
                                placeholder="Title of the Reddit post",
                                label="Title",
                                visible=True,
                            )
                            self.post_text = gr.Textbox(
                                value="",
                                interactive=True,
                                placeholder="Body of the Reddit post",
                                label="Post",
                                visible=True,
                            )
                    with gr.Row():
                        self.summary_text = gr.Textbox(
                            value="",
                            interactive=False,
                            placeholder="",
                            label="Summary",
                            visible=True,
                        )
                    with gr.Row():
                        load_example = gr.Button("Load Example")
                        clear = gr.Button("Clear")
                        submit = gr.Button("Submit")
                        load_example_info = gr.Markdown(visible=False, value="")
                        submit_click_event = submit.click(
                            fn=self.summary,
                            inputs=[self.subreddit_text, self.title_text, self.post_text],
                            outputs=[self.summary_text],
                            queue=False,
                        )
                        load_example_event = load_example.click(
                            fn=self.load_example,
                            outputs=[load_example_info, self.subreddit_text, self.title_text, self.post_text],
                            queue=False,
                        )
                        clear.click(
                            self.clear,
                            [self.subreddit_text, self.title_text, self.post_text],
                            [self.subreddit_text, self.title_text, self.post_text],
                        )

                with gr.Column(scale=1):
                    with gr.Row():
                        topk_s = gr.Slider(
                            0,
                            40,
                            value=self.gen_args.top_k,
                            label="Top-k",
                            interactive=True,
                            step=1,
                        )
                        topp_s = gr.Slider(0, 1, value=self.gen_args.top_p, label="Top-p", interactive=True)
                        temp_s = gr.Slider(0, 1, value=self.gen_args.temperature, label="Temperature", interactive=True)
                        max_new_tokens_s = gr.Slider(
                            1, 128, value=self.gen_args.max_new_tokens, label="Max new tokens", interactive=True
                        )
                        do_sample_s = gr.Checkbox(label="Do Sample", checked=True)
                        update_button = gr.Button("Update Settings", label="Update")
                        update_info = gr.Markdown(visible=False, value="")

                        def update_settings(topk_s, topp_s, temp_s, max_new_tokens_s, do_sample_s):
                            self.gen_args.top_k = topk_s
                            self.gen_args.top_p = topp_s
                            self.gen_args.temperature = topp_s
                            self.gen_args.max_new_tokens = max_new_tokens_s
                            self.gen_args.do_sample = do_sample_s
                            print(self.gen_args)
                            return gr.update(
                                visible=True,
                                value="""<h3 style="color:green;text-align:center">Settings updated successfully</h3>""",
                            )

                        update_button.click(
                            update_settings,
                            inputs=[topk_s, topp_s, temp_s, max_new_tokens_s, do_sample_s],
                            outputs=[update_info],
                            show_progress=True,
                        )

        demo.queue(max_size=self.max_size, concurrency_count=self.concurrency_count)
        demo.launch()


if __name__ == "__main__":
    SummaryUI().launch()
