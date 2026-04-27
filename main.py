import os
import json
import logging
import random
import math
from uuid import uuid4
from functools import reduce

import dataset
from telegram import (
    InlineKeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackQueryHandler,
    ConversationHandler,
    InlineQueryHandler
)

# Set up logging
logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)
logger = logging.getLogger(__name__)

# --- POLL HANDLERS ---

class BasePollHandler:
    max_options = 10
    name = "Unconfigured Poll Type"
    desc = "Poll type description goes here."

    @staticmethod
    def options(poll):
        return [[]]

    @staticmethod
    def title(poll):
        return f"*{poll['title']}*"

    @staticmethod
    def evaluation(poll):
        return "Somebody messed up! This poll type is not configured."

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        pass

    @staticmethod
    def get_confirmation_message(poll, user):
        return "Nothing happened."

    @staticmethod
    def requires_extra_config(meta):
        return False

    @staticmethod
    def ask_for_extra_config(meta):
        return "Somebody messed up! This poll type is not configured properly."

    @staticmethod
    def register_extra_config(text, meta):
        pass

class BasicPollHandler(BasePollHandler):
    name = "Basic poll"
    desc = "A straightforward first-past-the-post poll."

    @staticmethod
    def options(poll):
        buttons = []
        for i, option in enumerate(poll['options']):
            votes = BasicPollHandler.num_votes(poll, i)
            buttons.append([{
                'text': "{}{}{}".format(option['text'],
                                        " - " if votes > 0 else "",
                                        votes if votes > 0 else ""),
                'callback_data': {'i': i},
            }])
        return buttons

    @staticmethod
    def evaluation(poll):
        message = ""
        for i, option in enumerate(poll['options']):
            message += "\n"
            message += "{}: {}".format(option['text'], BasicPollHandler.num_votes(poll, i))
        return message

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        old_vote = votes.pop(user, None)
        if old_vote is not None and str(old_vote) == str(callback_data['i']):
            pass
        else:
            votes[user] = callback_data['i']

    @staticmethod
    def get_confirmation_message(poll, user):
        votes = poll['votes']
        if user in votes:
            vote = votes[user]
            for option in poll['options']:
                if option['index'] == vote:
                    return "You voted for \"{}\".".format(option['text'])
        return "Your vote was removed."

    @staticmethod
    def num_votes(poll, i):
        return list(poll['votes'].values()).count(i) if 'votes' in poll else 0

class SubsetPollHandler(BasePollHandler):
    max_options = 4
    name = "Subset poll"
    desc = "Lets you vote for any subset of the available options"

    @staticmethod
    def options(poll):
        buttons = []
        set_opts = SubsetPollHandler.get_subsets_of(poll['options'])
        for set_opt in set_opts:
            index_set = [opt['index'] for opt in set_opt]
            title_set = [opt['text'] for opt in set_opt]
            votes = SubsetPollHandler.num_votes_on_set(poll, index_set)
            buttons.append([{
                'text': "{}{}{}".format(SubsetPollHandler.get_set_opt_text(title_set),
                                        " - " if votes > 0 else "",
                                        votes if votes > 0 else ""),
                'callback_data': {'i': index_set}
            }])
        return buttons

    @staticmethod
    def evaluation(poll):
        message = ""
        for option in poll['options']:
            message += "\n"
            message += "{}: {}".format(option['text'], SubsetPollHandler.num_votes_on_option(poll, option['index']))
        return message

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        old_vote = votes.pop(user, None)
        if old_vote is not None and old_vote == callback_data['i']:
            pass
        else:
            votes[user] = callback_data['i']

    @staticmethod
    def get_confirmation_message(poll, user):
        votes = poll['votes']
        if user in votes:
            vote = votes[user]
            opts = poll['options']
            vote_set = [opt['text'] for opt in opts if opt['index'] in vote]
            string = ",".join(vote_set) if vote_set else "nothing"
            return "You voted for {}.".format(string)
        return "Your vote was removed."

    @staticmethod
    def get_set_opt_text(title_set):
        return ','.join(title_set) if title_set else "None"

    @staticmethod
    def num_votes_on_option(poll, index):
        if 'votes' not in poll: return 0
        votes = poll['votes']
        num = 0
        for cast_vote in votes.values():
            if index in cast_vote: num += 1
        return num

    @staticmethod
    def num_votes_on_set(poll, index_set):
        if 'votes' not in poll: return 0
        votes = poll['votes']
        num = 0
        for cast_vote in votes.values():
            if cast_vote == index_set: num += 1
        return num

    @staticmethod
    def get_subsets_of(some_set):
        return reduce(lambda z, x: z + [y + [x] for y in z], some_set, [[]])

class InstantRunoffPollHandler(BasePollHandler):
    name = "Instant runoff poll"
    desc = "Lets you define an order of preference and picks the option which is preferred by most."

    @staticmethod
    def options(poll):
        buttons = [[{'text': "Clear my votes", 'callback_data': {'i': "C"}}]]
        for option in poll['options']:
            votes_per_rank = InstantRunoffPollHandler.get_votes_per_rank(poll, option['index'])
            vote_str = ",".join([str(v) for v in votes_per_rank])
            has_votes = max(votes_per_rank) > 0
            buttons.append([{
                'text': "{}{}{}{}".format(option['text'],
                                          " - (" if has_votes else "",
                                          vote_str if has_votes else "",
                                          ")" if has_votes else ""),
                'callback_data': {'i': option['index']}
            }])
        return buttons

    @staticmethod
    def get_votes_per_rank(poll, opt_index):
        num_opts = len(poll['options'])
        counts = [0] * num_opts
        for vote in poll.get('votes', {}).values():
            for i, opt_ind in enumerate(vote):
                if opt_ind == opt_index: counts[i] += 1
        return counts

    @staticmethod
    def evaluation(poll):
        votes = poll.get('votes', {})
        candidates = [opt['index'] for opt in poll['options']]
        if votes:
            elected = InstantRunoffPollHandler.run_election(candidates, list(votes.values()))
            elected_names = [InstantRunoffPollHandler.get_option_name_by_index(poll, el) for el in elected]
            message = "{}: {}".format(
                "Current winner" if len(elected_names) == 1 else "We have a tie",
                ",".join(elected_names)
            )
        else:
            message = "There are currently no votes."
        num_votes = len(votes)
        body = ("This is an instant runoff poll.\n"
                "You define an order of preference for the available options "
                "by clicking on them in that order. For evaluation, the lowest "
                "ranking candidate is eliminated until there is a clear winner. \n"
                "Make sure to select all options that would work for you, but "
                "don't select any of those that don't work.\n\n"
                "*{}*\n{} people voted so far").format(message, num_votes)
        return body

    @staticmethod
    def run_election(candidates, votes, skip_index=0):
        if not any([v[skip_index:] for v in votes]): return candidates
        elected = None
        quota = math.floor(len(votes) / 2) + 1
        while elected is None:
            counts = InstantRunoffPollHandler.count_votes(votes, candidates, skip_index)
            if not counts: return candidates
            max_votes = max(counts)
            if max_votes >= quota:
                elected = [candidates[i] for i, count in enumerate(counts) if count == max_votes]
            else:
                min_votes = min(counts)
                old_candidates = list(candidates)
                delete_pls = [candidates[i] for i, count in enumerate(counts) if count == min_votes]
                for candidate in delete_pls: candidates.remove(candidate)
                if not candidates:
                    return InstantRunoffPollHandler.run_election(old_candidates, votes, skip_index=skip_index + 1)
        return elected

    @staticmethod
    def count_votes(votes, candidates, skip_index):
        counts = [0] * len(candidates)
        for vote in votes:
            for preference in vote:
                if preference in candidates:
                    counts[candidates.index(preference)] += 1
                    break
        return counts

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        old_vote = votes.get(user, [])
        if callback_data['i'] == 'C':
            old_vote = []
        elif callback_data['i'] in old_vote:
            old_vote.remove(callback_data['i'])
        else:
            old_vote.append(callback_data['i'])
        if not old_vote:
            votes.pop(user, None)
        else:
            votes[user] = old_vote

    @staticmethod
    def get_confirmation_message(poll, user):
        votes = poll['votes']
        if user in votes:
            vote = votes[user]
            vote_names = [InstantRunoffPollHandler.get_option_name_by_index(poll, i) for i in vote]
            return "Your order of preference: {}".format(",".join(vote_names))
        return "Your vote was removed."

    @staticmethod
    def get_option_name_by_index(poll, index):
        for opt in poll['options']:
            if opt['index'] == index: return opt['text']
        return "Invalid option"

class InstantRunoffTieBreakPollHandler(InstantRunoffPollHandler):
    name = "Instant runoff poll with fallback tie-breaking"
    desc = "Like instant runoff, but tries extra hard to break ties."

    @staticmethod
    def evaluation(poll):
        votes = poll.get('votes', {})
        candidates = [opt['index'] for opt in poll['options']]
        if votes:
            elected = None
            quota = math.floor(len(votes) / 2) + 1
            temp_candidates = list(candidates)
            while elected is None:
                counts = InstantRunoffPollHandler.count_votes(list(votes.values()), temp_candidates, 0)
                if not counts: break
                max_votes = max(counts)
                if max_votes >= quota:
                    elected = [temp_candidates[i] for i, count in enumerate(counts) if count == max_votes]
                else:
                    min_votes = min(counts)
                    old_candidates = list(temp_candidates)
                    delete_pls = [temp_candidates[i] for i, count in enumerate(counts) if count == min_votes]
                    for candidate in delete_pls: temp_candidates.remove(candidate)
                    if not temp_candidates:
                        temp_candidates = old_candidates
                        tiered_votes = {c: InstantRunoffPollHandler.get_votes_per_rank(poll, c) for c in old_candidates}
                        for i in range(1, len(poll['options']) + 1):
                            max_candidate_vote = -1
                            current_best = []
                            for c, v in tiered_votes.items():
                                prefix_sum = sum(v[:i])
                                if prefix_sum > max_candidate_vote:
                                    max_candidate_vote = prefix_sum
                                    current_best = [c]
                                elif prefix_sum == max_candidate_vote:
                                    current_best.append(c)
                            tiered_votes = {c: tiered_votes[c] for c in current_best}
                            if len(current_best) == 1:
                                elected = current_best
                                break
                        if not elected: elected = list(tiered_votes.keys())
            elected_names = [InstantRunoffPollHandler.get_option_name_by_index(poll, el) for el in (elected or [])]
            message = "{}: {}".format("Current winner" if len(elected_names) == 1 else "We have a tie", ",".join(elected_names))
        else:
            message = "There are currently no votes."
        num_votes = len(votes)
        body = ("This is an instant runoff poll with tie breaking. \n"
                "You define an order of preference for the available options by clicking on them in that order. \n"
                "This poll uses a fall-back tie-breaking algorithm.\n\n"
                "*{}*\n{} people voted so far").format(message, num_votes)
        return body

class OpenPollHandler(BasicPollHandler):
    name = "Open poll"
    desc = "Like basic poll, but you can see who voted for what."

    @staticmethod
    def evaluation(poll):
        message = "This is an open poll. People will see what you voted for.\n"
        for i, option in enumerate(poll['options']):
            message += "\n*{}: {}*".format(option['text'], OpenPollHandler.num_votes(poll, i))
            users = OpenPollHandler.get_users_voting_for(poll, option)
            for user in users: message += "\n " + user
        return message

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        old_vote = votes.pop(user, None)
        if old_vote is not None and str(old_vote.get('data')) == str(callback_data['i']):
            pass
        else:
            votes[user] = {'data': callback_data['i'], 'name': name}

    @staticmethod
    def get_confirmation_message(poll, user):
        votes = poll['votes']
        if user in votes:
            vote = votes[user]
            for option in poll['options']:
                if option['index'] == vote.get('data'): return "You voted for \"{}\".".format(option['text'])
        return "Your vote was removed."

    @staticmethod
    def num_votes(poll, i):
        return [val.get('data') for val in poll['votes'].values()].count(i) if 'votes' in poll else 0

    @staticmethod
    def get_users_voting_for(poll, option):
        return [val.get('name') for val in poll['votes'].values() if val.get('data') == option['index']] if 'votes' in poll else []

class CustomDescriptionPollHandler(BasicPollHandler):
    name = "Basic poll with custom description"
    desc = "Like basic poll, but lets you add a custom text to the poll message."

    @staticmethod
    def evaluation(poll):
        message = poll['meta'].get('text', '') + "\n"
        for i, option in enumerate(poll['options']):
            message += "\n{}: {}".format(option['text'], CustomDescriptionPollHandler.num_votes(poll, i))
        return message

    @staticmethod
    def ask_for_extra_config(meta):
        return "Please enter the text to be displayed above your poll:"

    @staticmethod
    def register_extra_config(text, meta):
        meta['text'] = text

    @staticmethod
    def requires_extra_config(meta):
        return 'text' not in meta

class STVPollHandler(BasePollHandler):
    name = "Single transferable vote poll"
    desc = "Similar to instant runoff, but multiple choices will be elected."
    HOPEFUL, ELECTED, ELIMINATED = range(3)

    @staticmethod
    def options(poll):
        buttons = [[{'text': "Clear my votes", 'callback_data': {'i': "C"}}]]
        for option in poll['options']:
            votes_per_rank = STVPollHandler.get_votes_per_rank(poll, option['index'])
            vote_str = ",".join([str(v) for v in votes_per_rank])
            has_votes = max(votes_per_rank) > 0
            buttons.append([{
                'text': "{}{}{}{}".format(option['text'], " - (" if has_votes else "", vote_str if has_votes else "", ")" if has_votes else ""),
                'callback_data': {'i': option['index']}
            }])
        return buttons

    @staticmethod
    def get_votes_per_rank(poll, opt_index):
        num_opts = len(poll['options'])
        counts = [0] * num_opts
        for vote in poll.get('votes', {}).values():
            for i, opt_ind in enumerate(vote):
                if opt_ind == opt_index: counts[i] += 1
        return counts

    @staticmethod
    def evaluation(poll):
        votes = poll.get('votes', {})
        numopts = int(poll.get('meta', {}).get('numopts', 1))
        candidates = [opt['index'] for opt in poll['options']]
        quota = int(float(len(votes)) / float(numopts + 1)) + 1
        if votes:
            candidate_info = {c: {'votes': {}, 'status': STVPollHandler.HOPEFUL} for c in candidates}
            for voter, vote in votes.items():
                if vote: candidate_info[vote[0]]['votes'][voter] = 1.0
            elected, ties = STVPollHandler.run_election(quota, numopts, votes, candidate_info)
            elected_names = [STVPollHandler.get_option_name_by_index(poll, el) for el in elected]
            tied_names = [STVPollHandler.get_option_name_by_index(poll, el) for el in ties]
            message = "Current Top {}:\n".format(numopts)
            for name in elected_names: message += "• *{}*\n".format(name)
            if ties: message += "And in the end, we have a tie: \n*{}*\n".format(", ".join(tied_names))
        else:
            message = "There are currently no votes."
        num_votes = len(votes)
        body = "This is a STV poll.\n{}\n{} people voted so far".format(message, num_votes)
        return body

    @staticmethod
    def run_election(quota, seats, votes, candidate_info):
        active = [c for c, i in candidate_info.items() if i['status'] != STVPollHandler.ELIMINATED]
        if len(active) <= seats:
            return active, []
        elected_candidates = []
        for candidate, info in candidate_info.items():
            count = sum(info['votes'].values())
            if count >= quota and info['status'] == STVPollHandler.HOPEFUL:
                elected_candidates.append(candidate); info['status'] = STVPollHandler.ELECTED
            info['curr_vote_count'] = count

        eliminated_candidates = []
        if not elected_candidates:
            hopeful_counts = [i['curr_vote_count'] for i in candidate_info.values() if i['status'] == STVPollHandler.HOPEFUL]
            if hopeful_counts:
                minimum = min(hopeful_counts)
                lowest = [c for c, i in candidate_info.items() if i['status'] == STVPollHandler.HOPEFUL and i['curr_vote_count'] == minimum]
                for c in lowest: candidate_info[c]['status'] = STVPollHandler.ELIMINATED
                eliminated_candidates = lowest

        STVPollHandler.transfer_votes(votes, quota, candidate_info)
        hopeful = [c for c, i in candidate_info.items() if i['status'] == STVPollHandler.HOPEFUL]
        elected = [c for c, i in candidate_info.items() if i['status'] == STVPollHandler.ELECTED]
        if len(hopeful) + len(elected) <= seats:
            ties = eliminated_candidates if len(hopeful) + len(elected) < seats else []
            return elected + hopeful, ties
        return STVPollHandler.run_election(quota, seats, votes, candidate_info)

    @staticmethod
    def transfer_votes(votes, quota, candidate_info):
        for candidate, info in candidate_info.items():
            if info['status'] == STVPollHandler.HOPEFUL: continue
            retain_ratio = quota / info['curr_vote_count'] if (info['status'] == STVPollHandler.ELECTED and info['curr_vote_count'] > 0) else 0
            transfer_ratio = 1 - retain_ratio
            del_voters = []
            for voter, value in info['votes'].items():
                if retain_ratio == 0: del_voters.append(voter)
                else: info['votes'][voter] = value * retain_ratio
                vote = votes[voter]
                try:
                    idx = vote.index(candidate) + 1
                    while idx < len(vote) and candidate_info[vote[idx]]['status'] != STVPollHandler.HOPEFUL: idx += 1
                    if idx < len(vote): candidate_info[vote[idx]]['votes'][voter] = candidate_info[vote[idx]]['votes'].get(voter, 0) + value * transfer_ratio
                except ValueError: pass
            for v in del_voters: del info['votes'][v]

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        InstantRunoffPollHandler.handle_vote(votes, user, name, callback_data)

    @staticmethod
    def get_confirmation_message(poll, user):
        return InstantRunoffPollHandler.get_confirmation_message(poll, user)

    @staticmethod
    def get_option_name_by_index(poll, index):
        return InstantRunoffPollHandler.get_option_name_by_index(poll, index)

    @staticmethod
    def ask_for_extra_config(meta):
        return "Please specify how many options should be elected. Type a number:"

    @staticmethod
    def register_extra_config(text, meta):
        if text.strip().isdigit(): meta['numopts'] = text

    @staticmethod
    def requires_extra_config(meta):
        return 'numopts' not in meta

class OpenCustomDescriptionPollHandler(CustomDescriptionPollHandler):
    name = "Open poll with custom description"
    desc = "Like open poll, but lets you add a custom text to the poll message."

    @staticmethod
    def evaluation(poll):
        message = poll['meta'].get('text', '') + "\n"
        for i, option in enumerate(poll['options']):
            message += "\n*{}: {}*".format(option['text'], OpenPollHandler.num_votes(poll, i))
            users = OpenPollHandler.get_users_voting_for(poll, option)
            for user in users: message += "\n " + user
        return message

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        OpenPollHandler.handle_vote(votes, user, name, callback_data)

    @staticmethod
    def get_confirmation_message(poll, user):
        return OpenPollHandler.get_confirmation_message(poll, user)

class InstantRunoffCustomDescriptionPollHandler(CustomDescriptionPollHandler):
    name = "Instant runoff poll with custom description"
    desc = "Like instant runoff, but with a custom description"

    @staticmethod
    def evaluation(poll):
        votes = poll.get('votes', {})
        candidates = [opt['index'] for opt in poll['options']]
        if votes:
            elected = InstantRunoffPollHandler.run_election(candidates, list(votes.values()))
            elected_names = [InstantRunoffPollHandler.get_option_name_by_index(poll, el) for el in elected]
            message = "{}: {}".format("Current winner" if len(elected_names) == 1 else "We have a tie", ",".join(elected_names))
        else:
            message = "There are currently no votes."
        num_votes = len(votes)
        body = poll['meta'].get('text', '') + "\n\n*{}*\n{} people voted so far".format(message, num_votes)
        return body

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        InstantRunoffPollHandler.handle_vote(votes, user, name, callback_data)

    @staticmethod
    def get_confirmation_message(poll, user):
        return InstantRunoffPollHandler.get_confirmation_message(poll, user)

    @staticmethod
    def options(poll):
        return InstantRunoffPollHandler.options(poll)

class MultipleOptionsPollHandler(BasePollHandler):
    name = "Multiple options poll"
    desc = "Lets you vote for multiple options"

    @staticmethod
    def options(poll):
        buttons = [[{'text': "Clear my votes", 'callback_data': {'i': "C"}}]]
        for opt in poll['options']:
            votes = MultipleOptionsPollHandler.num_votes_on_option(poll, opt['index'])
            buttons.append([{
                'text': "{}{}{}".format(opt['text'], " - " if votes > 0 else "", votes if votes > 0 else ""),
                'callback_data': {'i': opt['index']}
            }])
        return buttons

    @staticmethod
    def evaluation(poll):
        message = ""
        for option in poll['options']:
            message += "\n{}: {}".format(option['text'], MultipleOptionsPollHandler.num_votes_on_option(poll, option['index']))
        return message

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        old_vote = votes.get(user, [])
        if callback_data['i'] == 'C': old_vote = []
        elif callback_data['i'] in old_vote: old_vote.remove(callback_data['i'])
        else: old_vote.append(callback_data['i'])
        if not old_vote: votes.pop(user, None)
        else: votes[user] = old_vote

    @staticmethod
    def get_confirmation_message(poll, user):
        votes = poll['votes']
        if user in votes:
            vote = votes[user]
            vote_set = [opt['text'] for opt in poll['options'] if opt['index'] in vote]
            return "You voted for {}.".format(",".join(vote_set) if vote_set else "nothing")
        return "Your vote was removed."

    @staticmethod
    def num_votes_on_option(poll, index):
        if 'votes' not in poll: return 0
        return sum(1 for v in poll['votes'].values() if index in v)

class OpenMultipleOptionsPollHandler(MultipleOptionsPollHandler):
    name = "Open multiple options poll"
    desc = "Lets you vote for multiple options, and people can see who voted for what."

    @staticmethod
    def evaluation(poll):
        message = "This is an open multiple choices poll. People will see what you voted for.\n"
        for i, option in enumerate(poll['options']):
            message += "\n*{}: {}*".format(option['text'], OpenMultipleOptionsPollHandler.num_votes_on_option(poll, i))
            users = OpenMultipleOptionsPollHandler.get_users_voting_for(poll, option)
            for user in users: message += "\n " + user
        return message

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        old_vote = votes.pop(user, None)
        if callback_data['i'] == 'C': pass
        elif old_vote is not None and callback_data['i'] in old_vote.get('data', []):
            old_vote['data'].remove(callback_data['i'])
            if old_vote['data']: votes[user] = old_vote
        elif old_vote is not None:
            if 'data' not in old_vote: old_vote['data'] = []
            old_vote['data'].append(callback_data['i'])
            votes[user] = old_vote
        else:
            votes[user] = {'name': name, 'data': [callback_data['i']]}

    @staticmethod
    def get_confirmation_message(poll, user):
        votes = poll['votes']
        if user in votes:
            vote = votes[user]
            data = vote.get('data', [])
            vote_set = [opt['text'] for opt in poll['options'] if opt['index'] in data]
            return "You voted for {}.".format(",".join(vote_set) if vote_set else "nothing")
        return "Your vote was removed."

    @staticmethod
    def get_users_voting_for(poll, option):
        return [val.get('name') for val in poll['votes'].values() if option['index'] in val.get('data', [])] if 'votes' in poll else []

    @staticmethod
    def num_votes_on_option(poll, index):
        if 'votes' not in poll: return 0
        return sum(1 for v in poll['votes'].values() if index in v.get('data', []))

class DoodlePollHandler(BasePollHandler):
    name = "Doodle"
    desc = "Lets you pick the preferred out of multiple options, with yes-no-ifneedbe answers"

    @staticmethod
    def options(poll):
        buttons = [[{'text': "Clear my votes", 'callback_data': {'i': "C"}}]]
        for opt in poll['options']:
            total = DoodlePollHandler.num_votes_on_option(poll, opt['index'])
            yes = DoodlePollHandler.num_yes_on_option(poll, opt['index'])
            inb = DoodlePollHandler.num_inb_on_option(poll, opt['index'])
            buttons.append([{
                'text': "{}{}{}{}{}".format(opt['text'], " - " if total > 0 else "", yes if total > 0 else "", "/" if inb > 0 else "", inb if inb > 0 else ""),
                'callback_data': {'i': opt['index']}
            }])
        nopes = DoodlePollHandler.num_cant_make_it(poll)
        buttons.append([{'text': "Can't make it{}{}".format(" - " if nopes > 0 else "", nopes if nopes > 0 else ""), 'callback_data': {'i': "N"}}])
        return buttons

    @staticmethod
    def evaluation(poll):
        message = ""
        best_opts = DoodlePollHandler.find_best(poll)
        for option in poll['options']:
            message += "\n" + ("> " if option['index'] in best_opts else "") + "{}: {} yes".format(option['text'], DoodlePollHandler.num_yes_on_option(poll, option['index']))
            inb = DoodlePollHandler.num_inb_on_option(poll, option['index'])
            if inb > 0: message += ", {} if need be".format(inb)
        message += "\n\n{} people voted so far".format(len(poll.get('votes', {})))
        return message

    @staticmethod
    def find_best(poll):
        votes = poll.get('votes')
        if not votes: return []
        max_votes = -1; best = []
        for opt in poll['options']:
            num = DoodlePollHandler.num_votes_on_option(poll, opt['index'])
            if num > max_votes: max_votes = num; best = [opt]
            elif num == max_votes: best.append(opt)
        min_inb = 999999; best_after = []
        for opt in best:
            num = DoodlePollHandler.num_inb_on_option(poll, opt['index'])
            if num < min_inb: min_inb = num; best_after = [opt['index']]
            elif num == min_inb: best_after.append(opt['index'])
        return best_after

    @staticmethod
    def handle_vote(votes, user, name, callback_data):
        old_vote = votes.pop(user, None)
        pressed = str(callback_data['i'])
        if pressed == 'C': pass
        elif pressed == 'N':
            if old_vote != "nope": votes[user] = "nope"
        elif old_vote is not None and old_vote != "nope" and pressed in old_vote:
            if old_vote[pressed] == "y": old_vote[pressed] = "i"; votes[user] = old_vote
            elif old_vote[pressed] == "i":
                old_vote.pop(pressed)
                if old_vote: votes[user] = old_vote
        elif old_vote is not None and old_vote != "nope":
            old_vote[pressed] = "y"; votes[user] = old_vote
        else: votes[user] = {pressed: "y"}

    @staticmethod
    def get_confirmation_message(poll, user):
        return "Your vote was registered." if user in poll.get('votes', {}) else "Your vote was removed."

    @staticmethod
    def num_votes_on_option(poll, index):
        return sum(1 for v in poll.get('votes', {}).values() if v != "nope" and str(index) in v)

    @staticmethod
    def num_yes_on_option(poll, index):
        return sum(1 for v in poll.get('votes', {}).values() if v != "nope" and isinstance(v, dict) and v.get(str(index)) == 'y')

    @staticmethod
    def num_inb_on_option(poll, index):
        return sum(1 for v in poll.get('votes', {}).values() if v != "nope" and isinstance(v, dict) and v.get(str(index)) == 'i')

    @staticmethod
    def num_cant_make_it(poll):
        return sum(1 for v in poll.get('votes', {}).values() if v == "nope")

# --- BOT CONFIGURATION ---

POLL_HANDLERS = {
    0: BasicPollHandler,
    1: SubsetPollHandler,
    2: InstantRunoffPollHandler,
    3: InstantRunoffTieBreakPollHandler,
    4: OpenPollHandler,
    5: CustomDescriptionPollHandler,
    6: STVPollHandler,
    7: OpenCustomDescriptionPollHandler,
    8: InstantRunoffCustomDescriptionPollHandler,
    9: MultipleOptionsPollHandler,
    10: OpenMultipleOptionsPollHandler,
    11: DoodlePollHandler
}

# Conversation states
NOT_ENGAGED, TYPING_TITLE, TYPING_TYPE, TYPING_OPTION, TYPING_META = range(5)
AFFIRMATIONS = ["Cool", "Nice", "Doing great", "Awesome", "Okey dokey", "Neat", "Whoo", "Wonderful", "Splendid"]

class PollBot:
    def __init__(self, db_url):
        self.db = dataset.connect(db_url)

    def start(self, update, context):
        update.message.reply_text('Hi! Please send me the title of your poll. (/cancel to exit)')
        return TYPING_TITLE

    def handle_title(self, update, context):
        context.user_data['title'] = update.message.text
        keyboard = [[h.name] for h in POLL_HANDLERS.values()]
        update.message.reply_text("{}! What kind of poll is it going to be?".format(random.choice(AFFIRMATIONS)),
                                  reply_markup=ReplyKeyboardMarkup(keyboard, one_time_keyboard=True))
        return TYPING_TYPE

    def handle_type(self, update, context):
        text = update.message.text
        polltype = next((i for i, h in POLL_HANDLERS.items() if h.name == text), None)
        if polltype is None:
            update.message.reply_text("Invalid poll type. Please choose from the keyboard.")
            return TYPING_TYPE
        context.user_data['type'] = polltype
        context.user_data['options'] = []
        context.user_data['meta'] = {}
        handler = POLL_HANDLERS[polltype]
        if handler.requires_extra_config(context.user_data['meta']):
            update.message.reply_text(handler.ask_for_extra_config(context.user_data['meta']))
            return TYPING_META
        update.message.reply_text("{}. Now, send me the first answer option.".format(random.choice(AFFIRMATIONS)))
        return TYPING_OPTION

    def handle_meta(self, update, context):
        handler = POLL_HANDLERS[context.user_data['type']]
        handler.register_extra_config(update.message.text, context.user_data['meta'])
        if handler.requires_extra_config(context.user_data['meta']):
            update.message.reply_text(handler.ask_for_extra_config(context.user_data['meta']))
            return TYPING_META
        update.message.reply_text("{}, that's it! Next, please send me the first answer option.".format(random.choice(AFFIRMATIONS)))
        return TYPING_OPTION

    def handle_option(self, update, context):
        context.user_data['options'].append(update.message.text)
        handler = POLL_HANDLERS[context.user_data['type']]
        if len(context.user_data['options']) >= handler.max_options:
            return self.handle_done(update, context)
        update.message.reply_text("{}! Send another option or type /done.".format(random.choice(AFFIRMATIONS)))
        return TYPING_OPTION

    def handle_done(self, update, context):
        if not context.user_data.get('options'):
            update.message.reply_text("You need at least one option!")
            return TYPING_OPTION
        poll = {
            'poll_id': str(uuid4()),
            'title': context.user_data['title'],
            'type': context.user_data['type'],
            'options': [{'text': opt, 'index': i} for i, opt in enumerate(context.user_data['options'])],
            'meta': context.user_data.get('meta'),
        }
        self.db['setpolls'].insert(self.serialize(poll))
        update.message.reply_text(self.assemble_message_text(poll), reply_markup=self.assemble_inline_keyboard(poll, True), parse_mode='Markdown')
        context.user_data.clear()
        return NOT_ENGAGED

    def serialize(self, poll):
        ser = dict(poll)
        ser['options'] = json.dumps(poll['options'])
        if 'votes' in ser: ser['votes'] = json.dumps(poll['votes'])
        if 'meta' in ser: ser['meta'] = json.dumps(poll['meta'])
        return ser

    def deserialize(self, serialized):
        poll = dict(serialized)
        poll['options'] = json.loads(serialized['options'])
        poll['votes'] = json.loads(serialized.get('votes', '{}'))
        poll['meta'] = json.loads(serialized.get('meta', '{}')) if serialized.get('meta') else {}
        return poll

    def assemble_message_text(self, poll):
        handler = POLL_HANDLERS[poll['type']]
        return '{}\n{}'.format(handler.title(poll), handler.evaluation(poll))

    def assemble_inline_keyboard(self, poll, include_publish=False):
        handler = POLL_HANDLERS[poll['type']]
        items = handler.options(poll)
        buttons = []
        for row in items:
            current_row = []
            for item in row:
                item['callback_data']['id'] = poll['poll_id']
                current_row.append(InlineKeyboardButton(item['text'], callback_data=json.dumps(item['callback_data'])))
            buttons.append(current_row)
        if include_publish:
            buttons.append([InlineKeyboardButton("Publish!", switch_inline_query=poll['poll_id'])])
        return InlineKeyboardMarkup(buttons)

    def button(self, update, context):
        query = update.callback_query
        data = json.loads(query.data)
        table = self.db['setpoll_instances']
        templates = self.db['setpolls']
        kwargs = {}
        include_publish = False
        if query.message:
            if query.message.from_user.id == context.bot.id: include_publish = True
            kwargs = {'message_id': query.message.message_id, 'chat_id': query.message.chat.id}
            result = table.find_one(**kwargs) or templates.find_one(poll_id=data['id'])
        else:
            kwargs = {'inline_message_id': query.inline_message_id}
            result = table.find_one(**kwargs) or templates.find_one(poll_id=data['id'])

        if result and 'votes' not in result:
            result = dict(result); result.update(kwargs); result['votes'] = '{}'; result.pop('id', None)

        if not result:
            query.answer("Poll not found.")
            return

        poll = self.deserialize(result)
        handler = POLL_HANDLERS[poll['type']]
        handler.handle_vote(poll['votes'], str(query.from_user.id), query.from_user.first_name, data)
        query.answer(handler.get_confirmation_message(poll, str(query.from_user.id)))

        ser = self.serialize(poll)
        if 'id' in result: table.update(ser, ['id'])
        else: table.insert(ser)

        context.bot.edit_message_text(text=self.assemble_message_text(poll), parse_mode='Markdown', reply_markup=self.assemble_inline_keyboard(poll, include_publish), **kwargs)

    def inline_query(self, update, context):
        query = update.inline_query.query
        if not query: return
        results = list(self.db['setpolls'].find(poll_id=query))
        if not results:
            # Fallback to title search
            try:
                table_name = self.db['setpolls'].table.name
                query_str = f"SELECT * FROM {table_name} WHERE title LIKE :q"
                results = list(self.db.query(query_str, {'q': f'%{query}%'}))
            except:
                results = []

        inline_results = []
        for res in results:
            poll = self.deserialize(res)
            inline_results.append(InlineQueryResultArticle(id=poll['poll_id'], title=poll['title'], input_message_content=InputTextMessageContent(self.assemble_message_text(poll), parse_mode='Markdown'), reply_markup=self.assemble_inline_keyboard(poll)))
        update.inline_query.answer(inline_results)

    def send_help(self, update, context):
        helptext = "I'm a poll bot!\n\n"
        for poll in POLL_HANDLERS.values(): helptext += "*{}*: _{}_\n".format(poll.name, poll.desc)
        update.message.reply_text(helptext, parse_mode="Markdown")

    def cancel(self, update, context):
        update.message.reply_text("Canceled.", reply_markup=ReplyKeyboardRemove())
        return NOT_ENGAGED

    def error(self, update, context):
        logger.warning('Update "%s" caused error "%s"', update, context.error)

def main():
    token = os.environ.get('TOKEN')
    db_url = os.environ.get('DATABASE_URL', 'sqlite:///votes.db')
    if not token:
        logger.error("Please set the TOKEN environment variable")
        return

    bot_instance = PollBot(db_url)
    updater = Updater(token)
    dp = updater.dispatcher

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('newpoll', bot_instance.start), MessageHandler(Filters.text & ~Filters.command, bot_instance.handle_title)],
        states={
            TYPING_TITLE: [MessageHandler(Filters.text & ~Filters.command, bot_instance.handle_title)],
            TYPING_TYPE: [MessageHandler(Filters.text & ~Filters.command, bot_instance.handle_type)],
            TYPING_META: [MessageHandler(Filters.text & ~Filters.command, bot_instance.handle_meta)],
            TYPING_OPTION: [CommandHandler('done', bot_instance.handle_done), MessageHandler(Filters.text & ~Filters.command, bot_instance.handle_option)]
        },
        fallbacks=[CommandHandler('cancel', bot_instance.cancel), CommandHandler('done', bot_instance.handle_done)],
        allow_reentry=True
    )

    dp.add_handler(CommandHandler("start", bot_instance.start))
    dp.add_handler(CommandHandler("help", bot_instance.send_help))
    dp.add_handler(conv_handler)
    dp.add_handler(InlineQueryHandler(bot_instance.inline_query))
    dp.add_handler(CallbackQueryHandler(bot_instance.button))
    dp.add_error_handler(bot_instance.error)

    PORT = int(os.environ.get('PORT', '8443'))
    if 'RENDER_EXTERNAL_URL' in os.environ:
        url = os.environ['RENDER_EXTERNAL_URL']
        if not url.endswith('/'): url += '/'
        updater.start_webhook(listen="0.0.0.0", port=PORT, url_path=token, webhook_url=url + token)
    elif os.environ.get('PORT'):
        # Health check dummy server for Render Web Service when using polling
        from http.server import HTTPServer, BaseHTTPRequestHandler
        import threading
        class HealthCheckHandler(BaseHTTPRequestHandler):
            def do_GET(self):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"OK")
            def log_message(self, format, *args): return

        def run_health_check():
            try:
                httpd = HTTPServer(('0.0.0.0', PORT), HealthCheckHandler)
                httpd.serve_forever()
            except Exception as e:
                logger.error(f"Health check server failed: {e}")

        threading.Thread(target=run_health_check, daemon=True).start()
        updater.start_polling()
    else:
        updater.start_polling()

    logger.info("Bot started!")
    updater.idle()

if __name__ == '__main__':
    main()
