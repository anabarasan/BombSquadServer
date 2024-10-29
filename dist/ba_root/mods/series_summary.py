"""
series_summary module for BombSquad version 1.6.5
Provides functionality for storing match results at the end of a series
"""

from datetime import datetime
import os
import threading

import requests

import ba
from ba._gameresults import GameResults

import mysettings

SUMMARY_HTML = """<!doctype html>
<html>
  <head>
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Vaaichol Veerargal</title>
    <link
      href="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/css/bootstrap.min.css"
      rel="stylesheet"
      integrity="sha384-1BmE4kWBq78iYhFldvKuhfTAU6auU8tT94WrHftjDbrCEXSU1oBoqyl2QvZ6jIW3"
      crossorigin="anonymous"
    />
    <link
      rel="stylesheet"
      href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.7.0/font/bootstrap-icons.css"
    />
    <script
      src="https://cdn.jsdelivr.net/npm/bootstrap@5.1.3/dist/js/bootstrap.bundle.min.js"
      integrity="sha384-ka7Sk0Gln4gmtz2MlQnikT1wXgYsOg+OMhuP+IlRH9sENBO0LRn5q+8nbTov4+1p"
      crossorigin="anonymous"
    ></script>
  </head>
  <body class="container">
    <h1 style="text-align: center">
      &#x0BB5;&#x0BBE;&#x0BAF;&#x0BCD;&#x0B9A;&#x0BCD;&#x0B9A;&#x0BCA;&#x0BB2;&#x0BCD;
      &#x0BB5;&#x0BC0;&#x0BB0;&#X0BB0;&#X0BCD;&#x0B95;&#x0BB3;&#X0BCD;
    </h1>
    <h2 style="text-align:center">series at <<<series_time_stamp>>></h2>
    <h3 style="text-align:center">won by <<<winning_team_name>>></h3>
    <br/><br/>
    <<<details>>>
  </body>
</html>
"""


class SeriesSummary:
    match_results = []
    winning_sessionteam = None
    most_valuable_player = (None, None, None)
    most_violent_player = (None, None, None)
    most_violated_player = (None, None, None)

    @classmethod
    def append(cls, results: GameResults, stats: ba.Stats) -> None:
        data = {}
        player_scores = {}
        for player in stats.get_records().values():
            player_id = player.player.get_account_id()
            player_scores[player.getname(True)] = (
                player.accumscore,
                player.accum_kill_count,
                player.accum_killed_count,
            )

        winning_team = results.winning_sessionteam
        data["winner"] = winning_team.name.evaluate() if winning_team else "no winner"
        data["teams"] = []

        for team in results.sessionteams:
            players = []
            for player in team.players:
                name = player.getname(True)
                score, kill_count, killed_count = player_scores.get(
                    name, ("-", "-", "-")
                )
                players.append((name, score, kill_count, killed_count))

            data["teams"].append(
                {
                    "name": team.name.evaluate(),
                    "score": results.get_sessionteam_score(team)
                    if results.get_sessionteam_score(team)
                    else 0,
                    # score_str = results.get_sessionteam_score_str(team).evaluate(),
                    "players": players,
                }
            )
        cls.match_results.append(data)

    @classmethod
    def save_summary(cls):
        now = datetime.strftime(datetime.now(), "%Y%m%d%H%m%S%f")
        filename = os.path.join(mysettings.series_dir, f"{now}.html")
        indexfile = os.path.join(mysettings.series_dir, "index.html")
        print(f"saving summary to {filename}")
        html = ""
        if cls.most_valuable_player[1]:
            html += f"""<div class="row">&#x1F947; Most Valuable Player {cls.most_valuable_player[1]}</div>"""
        if cls.most_violent_player[1]:
            html += f"""<div class="row">&#x2620; Most Violent Player {cls.most_violent_player[1]} ({cls.most_violent_player[2]} kills)</div>"""
        if cls.most_violated_player[1]:
            html += f"""<div class="row">&#x1F637; Most Violated Player {cls.most_violated_player[1]} ({cls.most_violated_player[2]} deaths)</div>"""
        if (
            cls.most_valuable_player[1]
            or cls.most_violent_player[1]
            or cls.most_violated_player[1]
        ):
            html += "<br/><br/>"
        for idx, match_result in enumerate(cls.match_results):
            winner = match_result["winner"]
            html += """<div class="row">"""
            html += f"""<div class="row"><h3>Match {idx +1} </h3></div>"""
            # html += f"""<div class="row"><h3>Won by {winner}</h3></div>"""
            html += """<div class="row">"""
            for team in match_result["teams"]:
                html += """<div class="col">"""
                html += """<div class="row">"""
                # html += f"""{team["name"]} ({team["score"]})"""
                html += f"""{f'<i class="bi bi-trophy warning">&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;{team["name"]} ({team["score"]})</i>' if team["name"] == winner else f'{team["name"]} ({team["score"]})'}"""
                html += """</div>"""
                for player, player_score, kill_count, killed_count in team["players"]:
                    html += """<div class="row">"""
                    html += f"""{player} ({player_score})"""
                    html += """</div>"""
                html += """</div>"""
            html += """</div>"""
            html += "</div>"
            html += "<br/><hr/><br/>"
        html = (
            SUMMARY_HTML.replace("<<<details>>>", html)
            .replace("<<<series_time_stamp>>>", now)
            .replace("<<<winning_team_name>>>", cls.winning_sessionteam.name.evaluate())
        )

        with open(filename, "w") as f:
            f.write(html)

        with open(indexfile, "a") as f:
            f.write(f"<a href={now}.html>{now}</a><br/>")

        data = {}
        data["winner"] = cls.winning_sessionteam.name.evaluate()
        data["valuable_player"] = (
            cls.most_valuable_player[1] if cls.most_valuable_player[1] else ""
        )
        data["violent_player"] = (
            f"{cls.most_violent_player[1]} (kills = {cls.most_violent_player[2]})"
            if cls.most_violent_player[1]
            else ""
        )
        data["violated_player"] = (
            f"{cls.most_violated_player[1]} (deaths = {cls.most_violated_player[2]})"
            if cls.most_violated_player[1]
            else ""
        )
        data["matches"] = cls.match_results

        if mysettings.stats_server:
            PostToStatsServer(data).start()

        if mysettings.webhook_url:
            PostToMsTeams(data).start()

        cls.match_results = []
        cls.winning_sessionteam = None
        cls.most_valuable_player = (None, None, None)
        cls.most_violent_player = (None, None, None)
        cls.most_violated_player = (None, None, None)


class PostToStatsServer(threading.Thread):
    def __init__(self, summary):
        threading.Thread.__init__(self)
        self._summary = summary

    def run(self):
        response = requests.post(
            f"{mysettings.stats_server}/summary",
            json=self._summary,
            headers={"Content-Type": "application/json"},
        )

        response.raise_for_status()


class PostToMsTeams(threading.Thread):
    def __init__(self, summary):
        threading.Thread.__init__(self)
        self._summary = summary

    def run(self):
        payload = {
            "type": "message",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.adaptive",
                    "contentUrl": None,
                    "content": self.prepare_adaptive_card_json(),
                }
            ],
        }
        import json

        print("MsTeams Adaptive Card Data")
        print(json.dumps(payload))
        try:
            response = requests.post(
                url=mysettings.webhook_url,
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        except Exception as err:
            print(err)

    def prepare_adaptive_card_json(self):
        data = self._summary
        card = {
            "type": "AdaptiveCard",
            "body": [
                {
                    "type": "FactSet",
                    "facts": [
                        {"title": "Result:", "value": f"{data['winner']} Wins"},
                        {
                            "title": "Most Valuable Player",
                            "value": data["valuable_player"],
                        },
                        {
                            "title": "Most Violent Player",
                            "value": data["violent_player"],
                        },
                        {
                            "title": "Most Violated Player",
                            "value": data["violated_player"],
                        },
                    ],
                },
            ],
            "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
            "version": "1.4",
        }

        for idx, match in enumerate(data["matches"]):
            details = []
            # match number
            details.append(
                {
                    "type": "TextBlock",
                    "size": "Medium",
                    "weight": "Bolder",
                    "text": f"Match {idx+1}",
                    "wrap": False,
                    "style": "heading",
                }
            )
            # match winning team name
            details.append(
                {
                    "type": "FactSet",
                    "facts": [{"title": "Winner:", "value": match["winner"]}],
                }
            )

            # team details
            for team in match["teams"]:
                details.append(
                    {"type": "TextBlock", "text": f"{team['name']} ({team['score']})"}
                )
                # player points table (v1.5)
                # player_details = {
                #     "type": "Table",
                #     "columns": [{"width": 3}, {"width": 1}, {"width": 1}, {"width": 1}],
                #     "rows": [
                #         {
                #             "type": "TableRow",
                #             "cells": [
                #                 {
                #                     "type": "TableCell",
                #                     "items": [
                #                         {
                #                             "type": "TextBlock",
                #                             "text": "Name",
                #                             "wrap": False,
                #                             "weight": "Bolder",
                #                             "style": "heading",
                #                         },
                #                     ],
                #                 },
                #                 {
                #                     "type": "TableCell",
                #                     "items": [
                #                         {
                #                             "type": "TextBlock",
                #                             "text": "Score",
                #                             "wrap": False,
                #                             "weight": "Bolder",
                #                             "style": "heading",
                #                         },
                #                     ],
                #                 },
                #                 {
                #                     "type": "TableCell",
                #                     "items": [
                #                         {
                #                             "type": "TextBlock",
                #                             "text": "Kills",
                #                             "wrap": False,
                #                             "weight": "Bolder",
                #                             "style": "heading",
                #                         },
                #                     ],
                #                 },
                #                 {
                #                     "type": "TableCell",
                #                     "items": [
                #                         {
                #                             "type": "TextBlock",
                #                             "text": "Deaths",
                #                             "wrap": False,
                #                             "weight": "Bolder",
                #                             "style": "heading",
                #                         },
                #                     ],
                #                 },
                #             ],
                #             "style": "accent",
                #         },
                #     ],
                # }
                # for player in team["players"]:
                #     player_details["rows"].append(
                #         {
                #             "type": "TableRow",
                #             "cells": [
                #                 {
                #                     "type": "TableCell",
                #                     "items": [
                #                         {
                #                             "type": "TextBlock",
                #                             "text": player["name"],
                #                             "wrap": False,
                #                         },
                #                     ],
                #                 },
                #                 {
                #                     "type": "TableCell",
                #                     "items": [
                #                         {
                #                             "type": "TextBlock",
                #                             "text": str(player["score"]),
                #                             "wrap": False,
                #                         },
                #                     ],
                #                 },
                #                 {
                #                     "type": "TableCell",
                #                     "items": [
                #                         {
                #                             "type": "TextBlock",
                #                             "text": str(player["kills"]),
                #                             "wrap": False,
                #                         },
                #                     ],
                #                 },
                #                 {
                #                     "type": "TableCell",
                #                     "items": [
                #                         {
                #                             "type": "TextBlock",
                #                             "text": str(player["deaths"]),
                #                             "wrap": False,
                #                         },
                #                     ],
                #                 },
                #             ],
                #         }
                #     )
                # details.append(player_details)

                # player points table (v1.4)
                details.append(
                    {
                        "type": "ColumnSet",
                        "columns": [
                            {
                                "type": "Column",
                                "items": [
                                    {"type": "TextBlock", "text": "Name"},
                                ],
                            },
                            {
                                "type": "Column",
                                "items": [
                                    {"type": "TextBlock", "text": "Score"},
                                ],
                            },
                            {
                                "type": "Column",
                                "items": [
                                    {"type": "TextBlock", "text": "Kills"},
                                ],
                            },
                            {
                                "type": "Column",
                                "items": [
                                    {"type": "TextBlock", "text": "Deaths"},
                                ],
                            },
                        ],
                        "style": "emphasis",
                        "seperator": True,
                    }
                )
                for player in team["players"]:
                    player_details = {
                        "type": "ColumnSet",
                        "columns": [
                            {
                                "type": "Column",
                                "items": [
                                    {"type": "TextBlock", "text": player[0]}  # name
                                ],
                            },
                            {
                                "type": "Column",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": str(player[1]),
                                    }  # score
                                ],
                            },
                            {
                                "type": "Column",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": str(player[2]),
                                    }  # kills
                                ],
                            },
                            {
                                "type": "Column",
                                "items": [
                                    {
                                        "type": "TextBlock",
                                        "text": str(player[3]),
                                    }  # deaths
                                ],
                            },
                        ],
                        "seperator": True,
                    }
                    details.append(player_details)

            card["body"].extend(details)

        return card
