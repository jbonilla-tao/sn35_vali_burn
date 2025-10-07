# The MIT License (MIT)
# Copyright © 2023 Yuma Rao
# TODO(developer): Set your name
# Copyright © 2023 <your name>

# Permission is hereby granted, free of charge, to any person obtaining a copy of this software and associated
# documentation files (the “Software”), to deal in the Software without restriction, including without limitation
# the rights to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies of the Software,
# and to permit persons to whom the Software is furnished to do so, subject to the following conditions:

# The above copyright notice and this permission notice shall be included in all copies or substantial portions of
# the Software.

# THE SOFTWARE IS PROVIDED “AS IS”, WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO
# THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
# THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION
# OF CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
# DEALINGS IN THE SOFTWARE.
import numpy as np
import os
from typing import List
import bittensor as bt


def reward(query: int, response: int) -> float:
    """
    Reward the miner response to the dummy request. This method returns a reward
    value for the miner, which is used to update the miner's score.

    Returns:
    - float: The reward value for the miner.
    """
    bt.logging.info(
        f"In rewards, query val: {query}, response val: {response}, rewards val: {1.0 if response == query * 2 else 0}"
    )
    return 1.0 if response == query * 2 else 0


def get_rewards(
    self,
    query: int,
    responses: List[float],
    miner_uids: np.ndarray,
) -> np.ndarray:
    """
    Returns an array of rewards for the given query and responses.
    Only the selected miner (based on SELECTED_MINER_HOTKEY) gets rewards, others get 0.

    Args:
    - query (int): The query sent to the miner.
    - responses (List[float]): A list of responses from the miner.
    - miner_uids (np.ndarray): Array of UIDs of the queried miners.

    Returns:
    - np.ndarray: An array of rewards for the given query and responses.
    """
    selected_hotkey = os.getenv("SELECTED_MINER_HOTKEY")
    
    # if not selected_hotkey:
    #     bt.logging.warning("SELECTED_MINER_HOTKEY not set, giving rewards to all miners")
    #     return np.array([reward(query, response) for response in responses])
    
    # Find the UID of the selected miner
    selected_uid = None
    for uid in range(self.metagraph.n.item()):
        if self.metagraph.hotkeys[uid] == selected_hotkey:
            selected_uid = uid
            break
    
    if selected_uid is None:
        bt.logging.warning(f"Selected miner with hotkey {selected_hotkey} not found, giving rewards to all miners")
        # return np.array([reward(query, response) for response in responses])
        raise ValueError(f"Selected miner with hotkey {selected_hotkey} not found")
    
    # Initialize rewards array with zeros
    rewards = np.zeros(len(responses))
    
    # Only give reward to the selected miner if it's in the queried miners
    for i, uid in enumerate(miner_uids):
        if uid == selected_uid:
            # rewards[i] = reward(query, responses[i])
            rewards[i] = 1.0
            bt.logging.info(f"Giving reward {rewards[i]} to selected miner UID {uid}")
    
    return rewards
