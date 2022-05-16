from pathlib import Path
from typing import Callable, Dict, Iterator, List, Union

import gin
import torch
from grpc import Call
from torch import nn
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
from tqdm import tqdm

from src.data import data_tools
from src.models.train_model import write_gin
from src.typehinting import GenericModel

Tensor = torch.Tensor


class BaseModel(nn.Module):
    def __init__(self, observations: int, horizon: int) -> None:
        super().__init__()
        self.flatten = nn.Flatten() # we have 3d data, the linear model wants 2D
        self.linear = nn.Linear(observations, horizon)

    def forward(self, x: Tensor) -> Tensor:
        x = self.flatten(x)
        x = self.linear(x)
        return x


@gin.configurable
class BaseRNN(nn.Module):
    def __init__(
        self, input_size: int, hidden_size: int, num_layers: int, horizon: int
    ) -> None:
        super().__init__()
        self.rnn = nn.RNN(
            input_size=input_size,
            hidden_size=hidden_size,
            batch_first=True,
            num_layers=num_layers,
        )
        self.linear = nn.Linear(hidden_size, horizon)
        self.horizon = horizon

    def forward(self, x: Tensor) -> Tensor:
        x, _ = self.rnn(x)
        last_step = x[:, -1, :]
        yhat = self.linear(last_step)
        return yhat






def trainbatches(
    model: GenericModel,
    dataloader: Iterator,
    optimizer: torch.optim.Optimizer,
    loss_fn: Callable,
):
    model.train()
    epochloss = 0.0
    for x, y in dataloader:
        optimizer.zero_grad()
        yhat = model(x)
        loss = loss_fn(yhat, y)
        loss.backward()
        optimizer.step()
        epochloss += loss.data.item()

    epochloss /= len(dataloader)
    return epochloss


def evalbatches(
    model: GenericModel,
    dataloader: Iterator,
    loss_fn: torch.optim.Optimizer,
    metrics: List[Callable],
):
    model.eval()
    test_loss = 0.0
    metric_dict: Dict[str, float] = {}
    for x, y in dataloader:
        yhat = model(x)
        loss = loss_fn(yhat, y)
        test_loss += loss.data.item()
        for m in metrics:
            metric_dict[str(m)] = metric_dict.get(str(m), 0.0) + m(y, yhat)

    datasize = len(dataloader)
    test_loss /= datasize
    for m in metric_dict:
        metric_dict[str(m)] = metric_dict[str(m)] / datasize
    return test_loss, metric_dict


@gin.configurable
def trainloop(
    epochs: int,
    model: GenericModel,
    metrics: List[Callable],
    optimizer: torch.optim.Optimizer,
    learning_rate: float,
    loss_fn: Callable,
    train_dataloader: DataLoader,
    test_dataloader: DataLoader,
    log_dir: Union[Path, str],
) -> GenericModel:
    optimizer_: torch.optim.Optimizer = optimizer(
        model.parameters(), lr=learning_rate
    )  # type: ignore
    log_dir = Path(log_dir)
    data_tools.clean_dir(log_dir)
    writer = SummaryWriter(log_dir=log_dir)

    for epoch in tqdm(range(epochs)):
        train_loss = trainbatches(
            model=model,
            dataloader=iter(train_dataloader),
            optimizer=optimizer_,
            loss_fn=loss_fn,
        )
        writer.add_scalar("Loss/train", train_loss, epoch)

        test_loss, metric_dict = evalbatches(
            model=model,
            dataloader=iter(test_dataloader),
            loss_fn=loss_fn,
            metrics=metrics,
        )

        writer.add_scalar("Loss/test", test_loss, epoch)
        for m in metric_dict:
            writer.add_scalar(f"metric/{m}", metric_dict[m], epoch)
    write_gin(log_dir)
    return model
