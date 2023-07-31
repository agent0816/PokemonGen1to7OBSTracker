import requests

headers = {
    'Accept': 'application/json',
}

response = requests.get('http://ifconfig.co/', headers=headers)
responsev4 = requests.get('https://ifconfig.me/ip')

# Der Text der Antwort
print(responsev4.text)

# Um die JSON-Daten der Antwort zu bekommen
data = response.json()
print(data['ip'])
