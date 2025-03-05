import os
import logging
from web3 import Web3
from eth_account import Account
import random
import time
import threading

# Настраиваем логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Устанавливаем RPC провайдер Monad Testnet
rpc_providers = ['https://testnet-rpc.monad.xyz']

# Подключаемся к сети Monad Testnet
w3 = None
for rpc in rpc_providers:
    try:
        w3 = Web3(Web3.HTTPProvider(rpc))
        if w3.isConnected():
            logging.info(f"Успешно подключено к Monad Testnet через {rpc}")
            break
    except Exception as e:
        logging.error(f"Ошибка подключения к {rpc}: {e}")
        continue

if not w3:
    raise Exception("Не удалось подключиться к сети Monad Testnet")

# Читаем приватные ключи из файла private.txt
try:
    with open('private.txt', 'r') as file:
        private_keys = [line.strip() for line in file if line.strip()]
    logging.info(f"Найдено ключей: {len(private_keys)}")
except FileNotFoundError:
    logging.error("Файл 'private.txt' не найден в текущей директории")
    raise

# Проверяем валидность приватных ключей и создаём списки
valid_private_keys = []
wallets = []
for key in private_keys:
    key_str = key[2:] if key.startswith('0x') else key  # Убираем 0x, если есть
    if len(key_str) != 64 or not all(c in '0123456789abcdefABCDEF' for c in key_str):
        logging.warning(f"Обнаружен невалидный приватный ключ (информация скрыта)")
        continue
    valid_private_keys.append(key_str)
    try:
        wallet = Account.from_key(key_str).address
        wallets.append(wallet)
        logging.info(f"Добавлен кошелек {wallet}")
    except Exception as e:
        logging.error(f"Ошибка создания адреса из ключа: {e}")
        continue

# Константы
CHECK_INTERVAL = 10  # Интервал проверки в секундах
TIMEOUT_SECONDS = 180  # 3 минуты таймаут
EXPLORER_URL = "https://testnet.monadexplorer.com/address/"
BASE_GAS_PRICE_GWEI = 50
GROUP_SIZE = 2  # 5 потоков по 2 кошелька

def send_transaction(from_wallet, to_wallet, private_key, gas_price_gwei, amount_mon):
    try:
        balance = w3.eth.get_balance(from_wallet)
        amount_to_send = w3.toWei(amount_mon, 'ether')
        logging.info(f"Баланс {from_wallet}: {w3.from_wei(balance, 'ether')} MON")

        if balance <= amount_to_send:
            logging.warning(f"Недостаточный баланс на {from_wallet}: {w3.from_wei(balance, 'ether')} MON")
            return None

        gas_price_wei = w3.toWei(gas_price_gwei, 'gwei')
        gas_limit = 21000
        gas_cost = gas_limit * gas_price_wei
        logging.info(f"Стоимость газа: {w3.from_wei(gas_cost, 'ether')} MON (Gas Price: {gas_price_gwei} Gwei)")

        if balance < (amount_to_send + gas_cost):
            logging.warning(f"Недостаточно средств для газа на {from_wallet}")
            return None

        transaction = {
            'to': to_wallet,
            'value': amount_to_send,
            'gas': gas_limit,
            'gasPrice': gas_price_wei,
            'nonce': w3.eth.get_transaction_count(from_wallet),
            'chainId': 10143
        }

        signed_txn = w3.eth.account.sign_transaction(transaction, private_key)
        txn_hash = w3.eth.send_raw_transaction(signed_txn.rawTransaction)
        logging.info(f"Транзакция {txn_hash.hex()} отправлена от {from_wallet} к {to_wallet}: "
                     f"{w3.from_wei(amount_to_send, 'ether')} MON (Gas Price: {gas_price_gwei} Gwei)")
        return txn_hash.hex()

    except Exception as e:
        logging.error(f"Ошибка при отправке транзакции от {from_wallet}: {e}")
        return None

def process_group(start_idx, end_idx, group_num):
    logging.info(f"Поток {group_num}: Обработка кошельков с {start_idx + 1} по {end_idx}")

    for i in range(start_idx, end_idx):
        from_wallet = wallets[i]
        if i == len(wallets) - 1:  # Последний кошелек в списке
            to_wallet = wallets[0]  # Зацикливаем на первый кошелек
        else:
            to_wallet = wallets[i + 1]
        private_key = valid_private_keys[i]

        # Вычисление суммы как процента от баланса
        balance = w3.eth.get_balance(from_wallet)
        balance_mon = float(w3.from_wei(balance, 'ether'))  # Баланс в MON, приведенный к float
        percent = random.uniform(0.03, 0.08)  # Случайный процент от 3% до 8%
        amount_mon = balance_mon * percent  # Сумма в MON для отправки

        gas_price_gwei = random.uniform(BASE_GAS_PRICE_GWEI, BASE_GAS_PRICE_GWEI + 5)
        initial_balance_to = w3.eth.get_balance(to_wallet)

        txn_hash = send_transaction(from_wallet, to_wallet, private_key, gas_price_gwei, amount_mon)
        if not txn_hash:
            logging.error(f"Поток {group_num}: Не удалось отправить транзакцию от {from_wallet}")
            continue

        # Проверка баланса получателя
        start_time = time.time()
        while time.time() - start_time < TIMEOUT_SECONDS:
            current_balance_to = w3.eth.get_balance(to_wallet)
            logging.info(f"Поток {group_num}: Проверка {to_wallet}: {w3.from_wei(current_balance_to, 'ether')} MON "
                         f"(см. {EXPLORER_URL}{to_wallet})")

            if current_balance_to > initial_balance_to:
                logging.info(f"Поток {group_num}: Средства успешно получены на {to_wallet}")
                break
            time.sleep(CHECK_INTERVAL)
        else:
            logging.warning(f"Поток {group_num}: Таймаут {TIMEOUT_SECONDS} сек истёк для {from_wallet}")
            from_balance = w3.eth.get_balance(from_wallet)
            if from_balance > w3.toWei(amount_mon, 'ether'):
                new_gas_price_gwei = gas_price_gwei * 2
                logging.info(f"Поток {group_num}: Повторная отправка от {from_wallet} с газом {new_gas_price_gwei} Gwei")
                retry_txn_hash = send_transaction(from_wallet, to_wallet, private_key, new_gas_price_gwei, amount_mon)
                if retry_txn_hash:
                    retry_start_time = time.time()
                    while time.time() - retry_start_time < TIMEOUT_SECONDS:
                        current_balance_to = w3.eth.get_balance(to_wallet)
                        if current_balance_to > initial_balance_to:
                            logging.info(f"Поток {group_num}: Повторная транзакция успешна для {to_wallet}")
                            break
                        time.sleep(CHECK_INTERVAL)
                    else:
                        logging.error(f"Поток {group_num}: Повторная транзакция от {from_wallet} не подтверждена")
            else:
                logging.warning(f"Поток {group_num}: Недостаточно средств на {from_wallet} для повторной попытки")

        time.sleep(random.randint(3, 7))  # Пауза между транзакциями в группе

def run_infinite_cycle():
    while True:
        threads = []
        num_groups = (len(wallets) + GROUP_SIZE - 1) // GROUP_SIZE  # Делим 10 кошельков на 5 групп по 2

        for group_num in range(num_groups):
            start_idx = group_num * GROUP_SIZE
            end_idx = min(start_idx + GROUP_SIZE, len(wallets))
            thread = threading.Thread(target=process_group, args=(start_idx, end_idx, group_num + 1))
            threads.append(thread)
            thread.start()

        # Ожидаем завершения всех потоков в текущем цикле
        for thread in threads:
            thread.join()

        logging.info("Цикл завершён, начинаем новый через 30 секунд")
        time.sleep(30)  # Пауза между циклами

if __name__ == "__main__":
    if len(wallets) != 10:  # Ожидаем ровно 10 кошельков
        logging.error(f"Ожидалось 10 кошельков, найдено {len(wallets)}")
    else:
        logging.info(f"Запуск бесконечного цикла для {len(wallets)} кошельков с 5 потоками")
        run_infinite_cycle()