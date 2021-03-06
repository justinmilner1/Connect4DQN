import logging
import random
from time import time
import numpy as np
import matplotlib.pyplot as plt
import tensorflow as tf
import players
from game import Game
from datetime import datetime
from pathlib import Path
import shutil
import os
from random import randrange

# Default Q-Player settings
layers_size = [128, 128, 128, 128, 128]
batch_size = 128
batches_to_q_target_switch = 1000
gamma = 0.95
tau = 1
memory_size = 100000
learning_rate = 0.0001

def train_selfplay(p1_name, p2_name, p1_max_ent, p2_max_ent, num_of_games=1e6, savedir='./models/test_training', restore_path=None):
    """
    Initiate a single training process of selfplay

    checkpoints are saved in models/selfplay/X
    where X is a number from 1-windowsize

    :param p1_name: String. Name of player 1 (will be used as file-name)
    :param p2_name: String. Name of player 2 (will be used as file-name)
    :param p1_max_ent: Boolean. Should player 1 use maximum-entropy learning
    :param p2_max_ent: Boolean. Should player 2 use maximum-entropy learning
    :param p2_type: String. The type of player player2 should be
    :param num_of_games: Number. Number of games to train on
    :param savedir: String. Path to save trained weights
    """
    random.seed(int(time()*1000))
    tf.compat.v1.reset_default_graph()
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    # Initialize players
    graph1 = tf.Graph()
    graph2 = tf.Graph()


    with graph1.as_default():
        #player1
        p1 = players.QPlayer(tf.compat.v1.Session(), hidden_layers_size=layers_size, learning_batch_size=batch_size,
                             gamma=gamma,
                             batches_to_q_target_switch=batches_to_q_target_switch, tau=tau,
                             memory_size=memory_size,
                             maximize_entropy=p1_max_ent)
        if restore_path:
            print("Restoring player1 model...")
            p1.restore(restore_path)
        else:
            print("Training player1 from scratch...")
    p1.name = p1_name

    #player2
    with graph2.as_default():
        p2 = players.QPlayer(tf.compat.v1.Session(), hidden_layers_size=layers_size, learning_batch_size=batch_size,
                             gamma=gamma,
                             batches_to_q_target_switch=batches_to_q_target_switch, tau=tau,
                             memory_size=memory_size,
                             maximize_entropy=p2_max_ent)
        if restore_path:
            print("Restoring player2 model...")
            p2.restore(restore_path)
        else:
            print("Training player2 from scratch...")
    p2.name = p2_name

    #Create/delete folders for selfplay models, save current model to each
    models_window_size = 30
    for num in range(0, models_window_size):
        path = './models/selfplay_models/' + str(num)
        if os.path.exists(path):
            shutil.rmtree(path)
        os.makedirs(path)
        savepoint = '{dir}/temp_model.ckpt'.format(dir=path)
        with graph1.as_default():
            p1.save(savepoint)

    total_rewards = {p1.name: 0, p2.name: 0}
    costs = {p1.name: [], p2.name: []}  # this will store the costs, so we can plot them later
    rewards = {p1.name: [], p2.name: []}  # same, but for the players total rewards

    epsilon_list = []
    eps = 0.9
    decay = 0.9999992
    min_epsilon = 0.1

    # Start playing
    num_of_games = int(num_of_games)
    train_start_time = time()
    save_step_frequency = 17500
    current_window = 0
    dir = ''
    for g in range(1,num_of_games+1):
        #create player
        if g % save_step_frequency == 0:    #if have met save step frequency, select new agent to play against, save current one, throw out oldest
            #delete old directory if exists, then recreate the directory
            dir = './models/selfplay_models/' + str(current_window)
            if os.path.exists(dir):
                shutil.rmtree(dir)
            os.makedirs(dir)

            #increment current_window
            if current_window < models_window_size:
                current_window += 1
            else:
                current_window = 0

            with graph2.as_default():
                # save current model to directory, increment current_window
                p2.save('{dir}/temp_model.ckpt'.format(dir=dir))

                #restore a random model to play against
                dir = './models/selfplay_models/' + str(randrange(models_window_size)) + '/temp_model.ckpt'
                p2.restore(dir)
            #print("Game number: ", g, " Dir restored: ", dir)

        # Calculate annealed epsilon
        eps = .15#max(min_epsilon, eps * decay)

        game = Game(p1,p2, p1_name, p2_name) if g%2==0 else Game(p2,p1, p2_name, p1_name)
        last_phases = {p1.name: None, p2.name: None}  # will be used to store the last state a player was in
        while not game.game_status()['game_over']:
            # if isinstance(game.active_player, players.Human): #this probably isn't needed in training
            #     game.print_board()
            #     print("{}'s turn:".format(game.active_player.name))

            # If this is not the first move, store in memory the transition from the last state
            # the active player saw to this one
            state = np.copy(game.board)
            if last_phases[game.active_player.name] is not None:
                memory_element = last_phases[game.active_player.name]
                memory_element['next_state'] = state #.reshape(-1)
                memory_element['game_over'] = False
                game.active_player.add_to_memory(memory_element)

            # Play and receive reward
            if game.active_player.name == 'player_A':
                action = int(game.active_player.select_cell(state, epsilon=eps))
            else:
                action = int(game.active_player.select_cell(state, epsilon=0))
            play_status = game.play(action)
            game_over = play_status['game_over']
            if play_status['invalid_move']:
                r = game.invalid_move_reward
            elif game_over:
                if play_status['winner'] == 0:
                    r = game.tie_reward
                else:
                    r = game.winning_reward
            else:
                r = 0

            # Store the current state in temporary memory
            last_phases[game.active_player.name] = {'state': state,
                                                      'action': action,
                                                      'reward': r}
            total_rewards[game.active_player.name] += r
            if r == game.winning_reward:
                total_rewards[game.inactive_player.name] += game.losing_reward

            # Activate learning procedure
            cost = None
            if game.active_player.name == 'player_A':                                #changed here to have only player_A learn
                cost = game.active_player.learn(learning_rate=learning_rate)
            if cost is not None:
                costs[game.active_player.name].append(cost)

            # Next player's turn, if game hasn't ended
            if not game_over:
                game.next_player()

        # Adding last phase for winning (active) player
        memory_element = last_phases[game.active_player.name]
        memory_element['next_state'] = np.zeros((6,7))
        memory_element['game_over'] = True
        game.active_player.add_to_memory(memory_element)

        # Adding last phase for losing (inactive) player
        memory_element = last_phases[game.inactive_player.name]
        memory_element['next_state'] = np.zeros((6,7))
        memory_element['game_over'] = True
        memory_element['reward'] = game.losing_reward if r == game.winning_reward else game.tie_reward
        game.inactive_player.add_to_memory(memory_element)

        # Print statistics
        period = 25000
        if g % int(period) == 0:
            epsilon_list.append(eps)
            print('Game: {g} | Number of Trainings: {t1},{t2} | Epsilon: {e} | Average Rewards - {p1}: {r1}, {p2}: {r2}'
                  .format(g=g, p1=p1.name, r1=total_rewards[p1.name]/period,
                          p2=p2.name, r2=total_rewards[p2.name]/period,
                          t1=len(costs[p1.name]), t2=len(costs[p2.name]), e=eps))
            rewards[p1.name].append(total_rewards[p1.name]/period)
            rewards[p2.name].append(total_rewards[p2.name]/period)
            total_rewards = {p1.name: 0, p2.name: 0}

        if g % 400000 == 0:
            with graph1.as_default():
                print("Doing face off cuz game #", g)
                savepoint = '{dir}/{name}{datetime}.ckpt'.format(dir=savedir, name=p1.name, datetime=datetime.now().strftime("%m-%d-%H:%M"))
                print("Saved in: ", savepoint)
                p1.save(savepoint)
                face_off('player_A', savepoint, 'Drunk', None, 1000)


    # Save trained model and shutdown Tensorflow sessions
    training_time = time() - train_start_time
    minutes = int(training_time // 60)
    seconds = int(training_time % 60)
    if seconds < 10:
        seconds = '0{}'.format(seconds)
    print('Training took {m}:{s} minutes'.format(m=minutes, s=seconds))

    # Plot graphs and close sessions
    cost_colors = {p1.name: 'b', p2.name: 'k'}
    reward_colors = {p1.name: 'g', p2.name: 'r'}
    graphs = {p1.name: graph1, p2.name: graph2}


    with graph1.as_default():
        #pp.save('{dir}/{name}.ckpt'.format(dir=savedir, name=pp.name))
        dt = datetime.now().strftime("%m-%d-%H:%M")
        savepoint = '{dir}/{name}{datetime}.ckpt'.format(dir=savedir, name=p1.name, datetime=dt)
        print("Saved in: ", savepoint)
        p1.save(savepoint)
        p1.shutdown()

    plt.scatter(range(len(costs[p1.name])), costs[p1.name], c=cost_colors[p1.name])
    plt.title('Cost of player {}'.format(p1.name))
    plt.show()
    plt.scatter(range(len(rewards[p1.name])), rewards[p1.name], c=reward_colors[p1.name])
    plt.title('Average rewards of player {}'.format(p1.name))
    plt.show()

    plt.scatter(range(len(costs[p1.name])), costs[p1.name], c=cost_colors[p1.name])
    plt.title('Cost of player {} [0,1]'.format(p1.name))
    plt.ylim(0,1)
    plt.show()
    plt.scatter(range(len(rewards[p1.name])), rewards[p1.name], c=reward_colors[p1.name])
    plt.title('Average rewards of player {} [-1,1]'.format(p1.name))
    plt.ylim(-1,1)
    plt.show()

    plt.scatter(range(len(epsilon_list)), epsilon_list)
    plt.title('Epsilon')
    plt.show()

def train(p1_name, p2_name, p1_max_ent, p2_max_ent, p2_type, num_of_games=1e6, savedir='./models/test_training', restore_path=None):
    """
    Initiate a single training process
    :param p1_name: String. Name of player 1 (will be used as file-name)
    :param p2_name: String. Name of player 2 (will be used as file-name)
    :param p1_max_ent: Boolean. Should player 1 use maximum-entropy learning
    :param p2_max_ent: Boolean. Should player 2 use maximum-entropy learning
    :param p2_type: String. The type of player player2 should be
    :param num_of_games: Number. Number of games to train on
    :param savedir: String. Path to save trained weights
    """
    random.seed(int(time()*1000))
    tf.compat.v1.reset_default_graph()
    logging.basicConfig(level=logging.INFO, format='%(message)s')

    # Initialize players
    graph1 = tf.Graph()
    graph2 = None


    with graph1.as_default():
        p1 = players.QPlayer(tf.compat.v1.Session(), hidden_layers_size=layers_size, learning_batch_size=batch_size,
                             gamma=gamma,
                             batches_to_q_target_switch=batches_to_q_target_switch, tau=tau, memory_size=memory_size,
                             maximize_entropy=p1_max_ent)
        if restore_path:
            print("Restoring model...")
            p1.restore(restore_path)
        else:
            print("Training from scratch...")

    p1.name = p1_name

    if p2_type == 'Novice':
        print("Player 2 will be a novice")
        p2 = players.Novice()
    elif p2_type == 'Drunk':
        print("player 2 will be drunk")
        p2 = players.Drunk()
    elif p2_type == "Minimax":
        print("Playing against Minimax")
        p2 = players.Minimax()
    else:
        print('Player 2 will be DQN')
        graph2 = tf.Graph()
        with graph2.as_default():
            p2 = players.QPlayer(tf.compat.v1.Session(), hidden_layers_size=layers_size, learning_batch_size=batch_size,
                                 gamma=gamma,
                                 batches_to_q_target_switch=batches_to_q_target_switch, tau=tau,
                                 memory_size=memory_size,
                                 maximize_entropy=p2_max_ent)
    p2.name = p2_name

    total_rewards = {p1.name: 0, p2.name: 0}
    costs = {p1.name: [], p2.name: []}  # this will store the costs, so we can plot them later
    rewards = {p1.name: [], p2.name: []}  # same, but for the players total rewards

    epsilon_list = []
    eps = 0.9
    decay = 0.9999
    min_epsilon = 0.01

    # Start playing
    num_of_games = int(num_of_games)
    train_start_time = time()
    for g in range(1,num_of_games+1):
        game = Game(p1,p2, p1_name, p2_name) if g%2==0 else Game(p2,p1, p2_name, p1_name)
        last_phases = {p1.name: None, p2.name: None}  # will be used to store the last state a player was in
        while not game.game_status()['game_over']:
            # if isinstance(game.active_player, players.Human): #this probably isn't needed in training
            #     game.print_board()
            #     print("{}'s turn:".format(game.active_player.name))

            # If this is not the first move, store in memory the transition from the last state
            # the active player saw to this one
            state = np.copy(game.board)
            if last_phases[game.active_player.name] is not None:
                memory_element = last_phases[game.active_player.name]
                memory_element['next_state'] = state #.reshape(-1)
                memory_element['game_over'] = False
                game.active_player.add_to_memory(memory_element)

            # Calculate annealed epsilon
            eps = max(min_epsilon, eps * decay)

            # Play and receive reward
            action = int(game.active_player.select_cell(state, epsilon=eps))
            play_status = game.play(action)
            game_over = play_status['game_over']
            if play_status['invalid_move']:
                r = game.invalid_move_reward
            elif game_over:
                if play_status['winner'] == 0:
                    r = game.tie_reward
                else:
                    r = game.winning_reward
            else:
                r = 0

            # Store the current state in temporary memory
            last_phases[game.active_player.name] = {'state': state,
                                                      'action': action,
                                                      'reward': r}
            total_rewards[game.active_player.name] += r
            if r == game.winning_reward:
                total_rewards[game.inactive_player.name] += game.losing_reward

            # Activate learning procedure
            cost = game.active_player.learn(learning_rate=learning_rate)
            if cost is not None:
                costs[game.active_player.name].append(cost)

            # Next player's turn, if game hasn't ended
            if not game_over:
                game.next_player()

        # Adding last phase for winning (active) player
        memory_element = last_phases[game.active_player.name]
        memory_element['next_state'] = np.zeros((6,7))
        memory_element['game_over'] = True
        game.active_player.add_to_memory(memory_element)

        #Adding last phase for losing (inactive) player
        memory_element = last_phases[game.inactive_player.name]
        memory_element['next_state'] = np.zeros((6,7))
        memory_element['game_over'] = True
        memory_element['reward'] = game.losing_reward if r == game.winning_reward else game.tie_reward
        game.inactive_player.add_to_memory(memory_element)

        # Print statistics
        period = 10.0#5000
        if g % int(period) == 0:
            epsilon_list.append(eps)
            print('Game: {g} | Number of Trainings: {t1},{t2} | Epsilon: {e} | Average Rewards - {p1}: {r1}, {p2}: {r2}'
                  .format(g=g, p1=p1.name, r1=total_rewards[p1.name]/period,
                          p2=p2.name, r2=total_rewards[p2.name]/period,
                          t1=len(costs[p1.name]), t2=len(costs[p2.name]), e=eps))
            rewards[p1.name].append(total_rewards[p1.name]/period)
            rewards[p2.name].append(total_rewards[p2.name]/period)
            total_rewards = {p1.name: 0, p2.name: 0}


    # Save trained model and shutdown Tensorflow sessions
    training_time = time() - train_start_time
    minutes = int(training_time // 60)
    seconds = int(training_time % 60)
    if seconds < 10:
        seconds = '0{}'.format(seconds)
    print('Training took {m}:{s} minutes'.format(m=minutes, s=seconds))

    # Plot graphs and close sessions
    cost_colors = {p1.name: 'b', p2.name: 'k'}
    reward_colors = {p1.name: 'g', p2.name: 'r'}
    graphs = {p1.name: graph1, p2.name: graph2}

    for pp in [p1]:
        with graphs[pp.name].as_default():
            #pp.save('{dir}/{name}.ckpt'.format(dir=savedir, name=pp.name))
            dt = datetime.now().strftime("%m-%d-%H:%M")
            savepoint = '{dir}/{name}{datetime}.ckpt'.format(dir=savedir, name=pp.name, datetime=dt)
            print("Saved in: ", savepoint)
            pp.save(savepoint)
            pp.shutdown()

        plt.scatter(range(len(costs[pp.name])), costs[pp.name], c=cost_colors[pp.name])
        plt.title('Cost of player {}'.format(pp.name))
        plt.show()
        plt.scatter(range(len(rewards[pp.name])), rewards[pp.name], c=reward_colors[pp.name])
        plt.title('Average rewards of player {}'.format(pp.name))
        plt.show()

        plt.scatter(range(len(costs[pp.name])), costs[pp.name], c=cost_colors[pp.name])
        plt.title('Cost of player {} [0,1]'.format(pp.name))
        plt.ylim(0,1)
        plt.show()
        plt.scatter(range(len(rewards[pp.name])), rewards[pp.name], c=reward_colors[pp.name])
        plt.title('Average rewards of player {} [-1,1]'.format(pp.name))
        plt.ylim(-1,1)
        plt.show()

        plt.scatter(range(len(eps)), eps)
        plt.title('Epsilon')
        plt.show()

def play(model_path, is_max_entropy):
    """
    Play a game against a model
    :param model_path: String. Path to the model
    :param is_max_entropy: Boolean. Does the model uses entropy maximization
    """
    random.seed(int(time()))
    graph2 = tf.Graph()
    with graph2.as_default():
        p1 = None
        if model_path == 'Novice':
            print("Player 2 will be a novice")
            p1 = players.Novice()
        elif model_path == 'Drunk':
            print("player 2 will be drunk")
            p1 = players.Drunk()
        elif model_path == "Minimax":
            print("Playing against Minimax")
            p1 = players.Minimax()
        else:
            print("Loading model...")
            p1 = players.QPlayer(hidden_layers_size=layers_size, learning_batch_size=batch_size, gamma=gamma, tau=tau,
                                 batches_to_q_target_switch=batches_to_q_target_switch, memory_size=memory_size,
                                 session=tf.compat.v1.Session(), maximize_entropy=is_max_entropy)

            p1.restore(model_path)

        p2 = players.Human()

        for g in range(4):
            print('STARTING NEW GAME (#{})\n-------------'.format(g))
            if g%2==0:
                game = Game(p1, p2, 'Player_A', 'Human')
                print("Computer is X (1)")
            else:
                game = Game(p2, p1, 'Human', 'Player_A')
                print("Computer is O (-1)")
            while not game.game_status()['game_over']:
                if isinstance(game.active_player, players.Human):
                    game.print_board()
                    print("{}'s turn:".format(game.current_player))
                state = np.copy(game.board)
                # Force Q-Network to select different starting positions if it plays first
                action = int(game.active_player.select_cell(state,epsilon=0.0)) if np.count_nonzero(game.board) > 0 or not isinstance(game.active_player,players.QPlayer) else random.randint(0,8)
                game.play(action)
                if not game.game_status()['game_over']:
                    game.next_player()
            print('-------------\nGAME OVER!')
            game.print_board()
            print(game.game_status())
            print('-------------')

def face_off(p1_name, p1_path, p2_name, p2_path, games_to_play=100):
    '''
    Test two models against eachother. Default for p2 is drunk. Add parameters to use model as p2.
    :p1_path: String. Full ckpt path of first player
    :p1_name
    :p2_path: String. Full ckpt path of second player
    :p2_name
    :games_to_play: Int. Number of games to simulate
    '''
    #Scoreboard
    tie = 'TIE'
    results = {p1_name: 0, p2_name: 0, tie: 0}
    invalid_move_count = {p1_name: 0, p2_name: 0}

    #initialize first player
    graph1 = tf.Graph()
    with graph1.as_default():
        p1 = None
        # choosing if player is drunk or another model
        if p1_name == "Drunk":
            print("P1 is drunk")
            p1 = players.Drunk()
        elif p1_name == "Novice":
            print("P1 is novice")
            p1 = players.Novice()
        elif p1_name == "Minimax":
            print("P1 is minimax")
            p1 = players.Minimax()
        else:
            print("P1 is DQN ", p1_name)
            p1 = players.QPlayer(hidden_layers_size=layers_size, learning_batch_size=batch_size, gamma=gamma, tau=tau,
                                 batches_to_q_target_switch=batches_to_q_target_switch, memory_size=memory_size,
                                 session=tf.compat.v1.Session(), maximize_entropy=True)
            p1.restore(p1_path)
        p1.name = "Player1"


        #
        # #######
        # p1 = players.QPlayer(hidden_layers_size=layers_size, learning_batch_size=batch_size, gamma=gamma, tau=tau,
        #                      batches_to_q_target_switch=batches_to_q_target_switch, memory_size=memory_size,
        #                      session=tf.compat.v1.Session(), maximize_entropy=False)
        # p1.restore(p1_path)
        # #########

        #initialize second player
        graph2 = tf.Graph()
        with graph2.as_default():
            p2 = None
            #choosing if player is drunk or another model
            if p2_name == "Drunk":
                print("P2 is drunk")
                p2 = players.Drunk()
            elif p2_name == "Novice":
                print("P2 is novice")
                p2 = players.Novice()
            elif p2_name == "Minimax":
                print("P2 is minimax")
                p2 = players.Minimax()
            else:
                print("P2 is DQN ", p2_name)
                p2 = players.QPlayer(hidden_layers_size=layers_size, learning_batch_size=batch_size, gamma=gamma, tau=tau,
                                 batches_to_q_target_switch=batches_to_q_target_switch, memory_size=memory_size,
                                 session=tf.compat.v1.Session(), maximize_entropy=True)
                p2.restore(p2_path)
            p2.name = "Player2"

            #play games
            print('Playing...')
            #print('----------')
            for g in range(games_to_play):
                #print("------------------------")
                #print("Game number ", g)
                # choosing who goes first
                if g % 2 == 1:
                    game = Game(p1, p2, p1_name, p2_name)
                else:
                    game = Game(p2, p1, p2_name, p1_name)

                # playing out individual game
                while not game.game_status()['game_over']:
                    state = np.copy(game.board)
                    action = int(game.active_player.select_cell(state, epsilon=0.0))
                    game.play(action)
                    if not game.game_status()['game_over']:
                        game.next_player()
                winner = game.game_status()['winner']
                winner_name = game.player1.name if winner == 1 else (game.player2.name if winner == -1 else tie)
                # print('GAME - player X: {p1}, player O: {p2} | Winner: {w}'.format(
                #     p1=game.player1.name, p2=game.player2.name, w=winner_name
                # ))
                results[winner_name] += 1

                if game.game_status()['invalid_move']:
                    invalid_move_count[game.active_player.name] += 1

    print(games_to_play, " games played.")
    print('Final results: {}'.format(results))
    s = sum(results.values())
    pcts = {k: str(int(10000*v/s)/100) + '%' for k,v in results.items()}
    print('Percents: {}'.format(pcts))
    print('Invalid move count: {}'.format(invalid_move_count))
    return results


def eps_tester():
    number_of_games = 2800000
    min_epsilon = .1
    eps = .9
    decay = .9999992
    eps_list = []

    for x in range(number_of_games):
        eps = max(min_epsilon, eps * decay)
        if x % 100 == 0:
            eps_list.append(eps)

    plt.scatter(range(len(eps_list)), eps_list)
    plt.title('Epsilon')
    plt.show()


def selfplay_models_eval():
    win_percents = []
    for x in range(0, 31):
        dir = './models/selfplay_models/' + str(x) + '/temp_model.ckpt'
        print("--------dir: ", dir, " -----------" )
        results = face_off('player_A', dir, 'Drunk', None, 500)
        s = sum(results.values())
        pcts = {k: int(10000 * v / s) / 100 for k, v in results.items()}
        win_percents.append(float(pcts['player_A']))
        print(" ")
    print("Average win percentage: ", sum(win_percents)/len(win_percents))



#eps_tester()

#print("Training!")
#train_selfplay('player_A','player_B', True, True, num_of_games=2800000, savedir='./models/selfplay_training', restore_path=None)
#selfplay_models_eval()

# print("face off!")
# print("-----------------------Trained against Drunk player 12hrs----------------------------")
# face_off('player_A', './models/selfplay_training/player_A01-05-23:41.ckpt', 'Drunk', None, 1000)
# print("-----------------------Trained selfplay 6 hrs----------------------------")
# face_off('player_A', './models/selfplay_training/player_A01-28-05:00.ckpt', 'Drunk', None, 1000)
# print("------------------------Trained selfplay 6 + 1 hrs ---------------------------")
# face_off('player_A', './models/selfplay_training/player_A01-28-10:07.ckpt', 'Drunk', None, 1000)
# print("------------------------Trained selfplay 6 + 1 + .5 hrs---------------------------")
# face_off('player_A', './models/selfplay_training/player_A01-28-10:43.ckpt', 'Drunk', None, 1000)
# print("------------------------Trained selfplay 6 + 1 + .5 + .5 hrs---------------------------")
# face_off('player_A', './models/selfplay_training/player_A01-28-11:15.ckpt', 'Drunk', None, 1000)
# print("------------------------Trained selfplay 6 + 1 + .5 + .5 + 3 hrs---------------------------")
# face_off('player_A', './models/selfplay_training/player_A01-28-14:52.ckpt', 'Drunk', None, 1000)
# print("------------------------Trained selfplay 6 + 1 + .5 + .5 + 3 + 1 hrs---------------------------")
# face_off('player_A', './models/selfplay_training/player_A01-28-16:28.ckpt', 'Drunk', None, 1000)
print("------------------------Trained selfplay 6 + 1 + .5 + .5 + 3 + 1 hrs---------------------------")
face_off('player_A', './models/test_training_c4/temp.pth.tar', 'Drunk', None, 1000)


# print("Playing game!")
# play('Novice', True)