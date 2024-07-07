from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Optional
import random

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Przechowywanie danych gry w pamięci
game = {
    "admin": "",
    "players": [],
    "roles": ["Mieszkaniec", "Mafia", "Lekarz", "Detektyw"],
    "game_state": {},
    "votes": {},
    "day_phase": True,
    "night_actions": {},
    "protected_player": None,
    "action_history": [],
    "alive_players": [],
    "dead_players": [],
    "started": False,
    "phase": "setup",
    "voting_results": {},
    "waiting_for_players": [],
    "night_results": {}  # Dodajemy night_results
}


class Player(BaseModel):
    name: str


class Action(BaseModel):
    player: str
    target: Optional[str] = None


@app.post("/add_player")
async def add_player(player: Player):
    if game["started"]:
        raise HTTPException(status_code=400, detail="Cannot add players after the game has started.")
    if player.name not in game["players"]:
        game["players"].append(player.name)
        game["alive_players"].append(player.name)
        return {"message": f"Player {player.name} added.", "players": game["players"]}
    return {"message": "Player already exists.", "players": game["players"]}


@app.post("/start_game")
async def start_game():
    if len(game["players"]) < 4:
        return {"error": "Not enough players to start the game."}

    # Assign roles
    game["game_state"].clear()
    random.shuffle(game["players"])
    num_mafia = len(game["players"]) // 4
    roles = ["Mafia"] * num_mafia + ["Lekarz", "Detektyw"] + ["Mieszkaniec"] * (len(game["players"]) - num_mafia - 2)
    random.shuffle(roles)

    for i, player in enumerate(game["players"]):
        game["game_state"][player] = roles[i]

    game["action_history"].clear()
    game["started"] = True
    game["phase"] = "day_vote"
    game["votes"] = {}
    game["night_actions"] = {}
    game["protected_player"] = None
    game["waiting_for_players"] = get_waiting_for_players("day_vote", game["alive_players"], game["votes"],
                                                          game["night_actions"], game["game_state"])
    return {"message": "Game started.", "roles": game["game_state"]}

def check_win_conditions():
    mafia_count = sum(1 for role in game["game_state"].values() if role == "Mafia")
    print(mafia_count)
    town_count = len(game["game_state"]) - mafia_count
    print(town_count)
    print(game["game_state"])
    if mafia_count == 0:
        return "Town wins!"
    if mafia_count >= town_count:
        return "Mafia wins!"
    return None


@app.post("/next_phase")
async def next_phase():
    actionable_roles = ["Mafia", "Lekarz", "Detektyw"]  # Only these roles can perform actions

    if game["phase"] == "day_vote":
        if len(game["votes"]) < len(game["alive_players"]):
            return {"error": "Not all players have voted."}
        game["phase"] = "day_results"
        vote_count = {player: list(game["votes"].values()).count(player) for player in game["alive_players"]}
        eliminated_player = max(vote_count, key=vote_count.get)
        if vote_count[eliminated_player] > len(game["alive_players"]) // 2:
            game["alive_players"].remove(eliminated_player)
            game["dead_players"].append(eliminated_player)
            game["game_state"].pop(eliminated_player)
            game["voting_results"] = {"eliminated": eliminated_player, "vote_count": vote_count}
            game["action_history"].append(f"{eliminated_player} was eliminated by vote.")
        else:
            game["voting_results"] = {"eliminated": None, "vote_count": vote_count}

    elif game["phase"] == "day_results":
        game["phase"] = "night_actions"

    elif game["phase"] == "night_actions":
        actionable_players = [player for player in game["alive_players"] if
                              game["game_state"][player] in actionable_roles]
        if len(game["night_actions"]) < len(actionable_players):
            return {"error": "Not all actionable players have performed their actions."}
        game["phase"] = "night_results"

        # Perform the night actions
        mafia_target = None
        for player, target in game["night_actions"].items():
            role = game["game_state"].get(player)
            if role == "Mafia":
                mafia_target = target
                game["night_results"][player] = f"You targeted {target}"

        if mafia_target and mafia_target != game["protected_player"]:
            eliminated = mafia_target
            game["alive_players"].remove(eliminated)
            game["dead_players"].append(eliminated)
            game["game_state"].pop(eliminated)
            game["action_history"].append(f"{eliminated} was killed by the Mafia.")
        else:
            game["action_history"].append("No one was killed last night.")

        # Store the results of night actions to be revealed in the next phase
        for player, target in game["night_actions"].items():
            role = game["game_state"].get(player)
            if role == "Detektyw":
                target_role = game["game_state"].get(target)
                game["night_results"][player] = f"Your investigation result: {target} is {target_role}"

    elif game["phase"] == "night_results":
        game["phase"] = "day_vote"
        game["votes"] = {}
        game["night_actions"] = {}
        game["protected_player"] = None
        game["night_results"] = {}  # Reset night results

    win_message = check_win_conditions()
    if win_message:
        game["action_history"].append(win_message)
        return {"message": win_message, "players": game["players"]}

    game["waiting_for_players"] = get_waiting_for_players(game["phase"], game["alive_players"], game["votes"],
                                                          game["night_actions"], game["game_state"])
    return {"message": f"Phase changed to {game['phase']}.", "phase": game["phase"]}


@app.post("/vote")
async def vote(action: Action):
    if action.player not in game["alive_players"] or action.target not in game["alive_players"]:
        return {"error": "Invalid player or target."}
    if action.player not in game["votes"]:
        game["votes"][action.player] = action.target
        game["action_history"].append(f"{action.player} voted for {action.target}")
        game["waiting_for_players"] = get_waiting_for_players(game["phase"], game["alive_players"], game["votes"],
                                                              game["night_actions"], game["game_state"])
        return {"message": f"{action.player} voted for {action.target}", "votes": game["votes"]}
    return {"error": f"{action.player} has already voted."}


@app.post("/action")
async def perform_action(action: Action):
    if action.player not in game["game_state"]:
        return {"error": "Player does not exist."}

    role = game["game_state"][action.player]
    action_message = ""
    if role == "Lekarz" and action.target:
        game["protected_player"] = action.target
        game["night_actions"][action.player] = action.target
        action_message = f"{action.player} (Lekarz) tries to save {action.target}"
    elif role == "Detektyw" and action.target:
        target_role = game["game_state"][action.target]
        game["night_actions"][action.player] = action.target
        action_message = f"{action.player} (Detektyw) investigates {action.target}"
    elif role == "Mafia" and action.target:
        game["night_actions"][action.player] = action.target  # Przypisujemy akcje do konkretnego gracza
        action_message = f"{action.player} (Mafia) targets {action.target}"

    if action_message:
        game["action_history"].append(action_message)
        game["waiting_for_players"] = get_waiting_for_players(game["phase"], game["alive_players"], game["votes"],
                                                              game["night_actions"], game["game_state"])
        return {"message": action_message}

    return {"message": f"No action performed by {action.player}"}

# Usuwamy sprawdzanie unikalności akcji mafii, ponieważ teraz każda akcja jest przypisywana do konkretnego gracza


def get_waiting_for_players(phase, alive_players, votes, night_actions, game_state):
    actionable_roles = ["Mafia", "Lekarz", "Detektyw"]
    if phase == "day_vote":
        return [player for player in alive_players if player not in votes]
    elif phase == "night_actions":
        return [player for player in alive_players if
                game_state[player] in actionable_roles and player not in night_actions]
    return []


@app.get("/game_state")
async def get_game_state():
    waiting_for_players = get_waiting_for_players(game["phase"], game["alive_players"], game["votes"],
                                                  game["night_actions"], game["game_state"])
    return {
        "players": game["players"],
        "alive_players": game["alive_players"],
        "dead_players": game["dead_players"],
        "game_state": game["game_state"],
        "votes": game["votes"],
        "day_phase": game["day_phase"],
        "phase": game["phase"],
        "action_history": game["action_history"],
        "protected_player": game["protected_player"],
        "voting_results": game["voting_results"],
        "waiting_for_players": waiting_for_players,
        "night_actions": game["night_actions"]  # Dodajemy night_results
    }
