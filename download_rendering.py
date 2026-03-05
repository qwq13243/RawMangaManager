import urllib.request
import ssl

# Create unverified context to avoid SSL errors if any
ssl._create_default_https_context = ssl._create_unverified_context

url = 'https://raw.githubusercontent.com/MashiroSaber03/Saber-Translator/main/src/core/rendering.py'
urllib.request.urlretrieve(url, 'temp_rendering.py')
print("Downloaded rendering.py")
