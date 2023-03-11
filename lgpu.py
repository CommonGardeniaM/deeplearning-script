# -*- coding: utf-8 -*-
"""p (3).ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1307lQH3hDtIxA0PiGxI1lza8dN2I1LA9
"""

import os
from itertools import chain

from transformers import AutoTokenizer, AutoModelForCausalLM
from datasets import load_dataset

from datetime import datetime
from typing import Optional

import datasets
import torch
from torch.utils.data import DataLoader, Dataset
from pytorch_lightning import LightningDataModule, LightningModule, Trainer, seed_everything
from pytorch_lightning.strategies import DeepSpeedStrategy
from torch.utils.data import DataLoader
from transformers import (
    AdamW,
    AutoConfig,
    AutoModelForSequenceClassification,
    AutoTokenizer,
    get_linear_schedule_with_warmup,
)

from deepspeed.ops.adam import DeepSpeedCPUAdam

class CreateDataModule(LightningDataModule):
    def __init__(self, batch_size=1, max_token_len=128,
                 pretrained_model='rinna/japanese-gpt2-xsmall'):
        super().__init__()
        self.batch_size = batch_size
        self.max_token_len = max_token_len
        self.tokenizer = AutoTokenizer.from_pretrained(pretrained_model)
        self.block_size = 128

    def setup(self, stage=None):
        self.raw_datasets = load_dataset("amazon_reviews_multi", "ja")

        self.tokenized_datasets = self.raw_datasets.map(
            self.tokenize_function,
            batched=True,
            num_proc=1,
            remove_columns=self.raw_datasets["train"].column_names,
            desc="Running tokenizer on dataset",
        )

        self.lm_datasets = self.tokenized_datasets.map(
            self.group_texts,
            batched=True,
            num_proc=1,
            desc=f"Grouping texts in chunks of {self.max_token_len}",
        )

        self.train_dataset = self.lm_datasets["train"].with_format("torch")
        self.vaild_dataset = self.lm_datasets["validation"].with_format("torch")
        self.test_dataset = self.lm_datasets["test"].with_format("torch")

    def tokenize_function(self, examples):
        to_tokenize = [f"{z}" for z in examples[TEXT_COLUMN]]
        return self.tokenizer(to_tokenize, max_length=128, truncation=True)

    def group_texts(self, examples):
        # Concatenate all texts.
        concatenated_examples = {k: list(chain(*examples[k])) for k in examples.keys()}
        total_length = len(concatenated_examples[list(examples.keys())[0]])
        # We drop the small remainder, we could add padding if the model supported it instead of this drop, you can
        # customize this part to your needs.
        if total_length >= self.block_size:
            total_length = (total_length // self.block_size) * self.block_size
        # Split by chunks of max_len.
        result = {
            k: [t[i : i + self.block_size] for i in range(0, total_length, self.block_size)]
            for k, t in concatenated_examples.items()
        }
        result["labels"] = result["input_ids"].copy()
        return result

    def train_dataloader(self):
        return DataLoader(self.train_dataset, batch_size=self.batch_size, shuffle=True, num_workers=1)

    def val_dataloader(self):
        return DataLoader(self.vaild_dataset, batch_size=self.batch_size, num_workers=1)

    def test_dataloader(self):
        return DataLoader(self.test_dataset, batch_size=self.batch_size, num_workers=os.cpu_count())



# TEXT_COLUMN = "review_body"
# data_module = CreateDataModule()
# data_module.setup("")

# import random
#
# for index in random.sample(range(len(data_module.train_dataset)), 1):
#     print(f"Sample {index} of the training set: {data_module.train_dataset[index]}.")

class GPTTransformer(LightningModule):
    def __init__(self, n_epochs=None, pretrained_model='rinna/japanese-gpt2-xsmall'):
        super().__init__()

        self.gpt = AutoModelForCausalLM.from_pretrained(pretrained_model, return_dict=True)
        self.tokenizer = AutoTokenizer.from_pretrained(pretrained_model)
        self.gpt.resize_token_embeddings(len(self.tokenizer))
        self.n_epochs = n_epochs
        self.lm_head = torch.nn.Linear(768, self.gpt.vocab_size, bias=False)
        self._loss = torch.nn.CrossEntropyLoss()

    def forward(self, **inputs):
        # return self.gpt(**inputs, labels=inputs["input_ids"])
        return self.gpt(**inputs)

    def loss(self, predictions: dict, labels: dict) -> torch.tensor:
        batch_logits = predictions["lm_logits"][..., :-1, :].contiguous()
        target_labels = labels["tokens"][..., 1:].contiguous()
        loss = self._loss(
            batch_logits.view(-1, batch_logits.size(-1)), target_labels.view(-1)
        )
        return loss

    def training_step(self, batch, batch_idx):
        inputs = batch
        model_out = self(**inputs)
        # loss_val = self.loss(model_out, inputs)
        loss_val = model_out.loss
        return loss_val

    def validation_step(self, batch, batch_idx):
        inputs = batch
        model_out = self(**inputs)
        # loss_val = self.loss(model_out, inputs)
        loss_val = model_out.loss

        output = loss_val

        return output

        def validation_epoch_end(self, outputs, mode="val"):
            losses = torch.cat(outputs)
            try:
                eval_loss = torch.mean(losses)
                perplexity = math.exp(eval_loss)
            except OverflowError:
                perplexity = float("inf")

            self.log("metric", "perplexity: {perplexity} eval_loss: {eval_loss}")
        return result

#     # def test_step(self, batch, batch_idx):
#     #     print(batch)
#     #     loss = self.forward(input_ids=batch["input_ids"],
#     #                                 attention_mask=batch["attention_mask"])
#     #     return outputs[0]

    # def test_epoch_end(self, outputs):
    #     print("test_epoch_end")
    #     return self.validation_epoch_end(outputs, "test")

    def configure_optimizers(self):
        no_decay = ["bias", "layer_norm.weight"]
        optimizer_grouped_parameters = [
            {
                "params": [p for n, p in model.named_parameters() if not any(nd in n for nd in no_decay)],
                "weight_decay": 0.01,
            },
            {
                "params": [p for n, p in model.named_parameters() if any(nd in n for nd in no_decay)],
                "weight_decay": 0.0,
            },
        ]
        # optimizer = torch.optim.Adam(optimizer_grouped_parameters, lr=1e-5)
        optimizer = DeepSpeedCPUAdam(optimizer_grouped_parameters, lr=1e-5)
        return [optimizer], []

if __name__ == '__main__':
    seed_everything(45)

    TEXT_COLUMN = "review_body"
    data_module = CreateDataModule()
    data_module.setup("")

    model = GPTTransformer(
        pretrained_model="rinna/japanese-gpt2-medium", n_epochs=1,
    )
    model.half()

    trainer = Trainer(
        max_epochs=1,
        accelerator="gpu",
#         auto_scale_batch_size=True,
        accumulate_grad_batches=2,
        strategy=DeepSpeedStrategy(offload_optimizer=True, stage=2, allgather_bucket_size=2e8, reduce_bucket_size=2e8),
        precision=16,
        devices=2,  # limiting got iPython runs
    )

    trainer.fit(model,  datamodule=data_module)
    print("finish")
