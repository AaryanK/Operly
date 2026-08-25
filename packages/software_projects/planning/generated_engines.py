"""Validated domain engines available to generated previews.

These are application-specific until separately reviewed for primitive status.
"""
from __future__ import annotations


def apply_match_events(home_club:str,away_club:str,events:list[dict])->dict:
    if home_club==away_club:raise ValueError("A club cannot play itself")
    score={home_club:0,away_club:0};active=set();dismissed=set();substituted_in=set();last_minute=-1
    for event in events:
        minute=int(event["minute"])
        if minute<last_minute:raise ValueError("Match events must be chronological")
        last_minute=minute;kind=event["type"];club=event.get("club");player=event.get("player")
        if club not in score:raise ValueError("Event club is not in this match")
        if kind=="goal":score[club]+=1
        elif kind=="lineup":active.add(player)
        elif kind=="red_card":dismissed.add(player);active.discard(player)
        elif kind=="substitution":
            incoming=event["incoming"];outgoing=event["outgoing"]
            if incoming in substituted_in:raise ValueError("A player cannot be substituted in twice")
            if outgoing in dismissed:raise ValueError("A dismissed player cannot be substituted")
            substituted_in.add(incoming);active.discard(outgoing);active.add(incoming)
        else:raise ValueError("Unsupported match event")
    return {"home":home_club,"away":away_club,"homeScore":score[home_club],"awayScore":score[away_club],"activePlayers":sorted(active),"dismissedPlayers":sorted(dismissed)}


def calculate_standings(clubs:list[str],results:list[dict])->list[dict]:
    table={club:{"club":club,"played":0,"won":0,"drawn":0,"lost":0,"goalsFor":0,"goalsAgainst":0,"points":0} for club in clubs}
    for result in results:
        home,away=result["home"],result["away"]
        if home==away or home not in table or away not in table:raise ValueError("Invalid fixture")
        hg,ag=int(result["homeScore"]),int(result["awayScore"])
        for club,gf,ga in ((home,hg,ag),(away,ag,hg)):
            row=table[club];row["played"]+=1;row["goalsFor"]+=gf;row["goalsAgainst"]+=ga
        if hg>ag:table[home]["won"]+=1;table[away]["lost"]+=1;table[home]["points"]+=3
        elif ag>hg:table[away]["won"]+=1;table[home]["lost"]+=1;table[away]["points"]+=3
        else:
            table[home]["drawn"]+=1;table[away]["drawn"]+=1;table[home]["points"]+=1;table[away]["points"]+=1
    rows=[]
    for row in table.values():row["goalDifference"]=row["goalsFor"]-row["goalsAgainst"];rows.append(row)
    return sorted(rows,key=lambda x:(-x["points"],-x["goalDifference"],-x["goalsFor"],x["club"]))
