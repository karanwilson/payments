# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method
from payments.utils import create_payment_gateway

from zeep import Client #, Settings
from base64 import b64decode
from oauthlib.common import urldecode
from Crypto.Cipher import AES
from datetime import datetime

class FSSettings(Document):
	supported_currencies = ["INR"]

	def	validate(self):
		create_payment_gateway("FS")
		call_hook_method("payment_gateway_enabled", gateway="FS")
		if not self.flags.ignore_mandatory:
			self.validate_fs_credentials()
	
	def	validate_fs_credentials(self):
		# login to SOAP Server
		self.fapi_login()
		self.request_transfer_token()

	def fapi_login(self):
		# Initialising the SOAP client
		global fs_client
		strPID = self.fs_user
		strPassword = self.get_password(fieldname="fs_password", raise_exception=False)
		fs_client = Client("assets/payments/FS.wsdl")

		login_res = fs_client.service.login(strPID, strPassword)
		self.login_status = login_res["Result"]
		if self.login_status == "OK":
			self.last_login_at = datetime.now()
		
		return login_res

	def request_transfer_token(self):
		param = [{"Req":""}]
		fs_client = Client("assets/payments/FS.wsdl")
		
		token_res = fs_client.service.requestTransferToken(param)
		self.token_response = token_res

		encrypted = urldecode(token_res)
		self.token_encrypted = encrypted[0][0]

		d = encrypted[0][0].split(";")
		self.encrypt_data = d[0]
		self.encrypt_iv = d[1]

		key = "fstockencryptkey".encode("utf8")
		data = b64decode(d[0])
		iv = b64decode(d[1])
		
		cipher = AES.new(key, AES.MODE_CBC, iv)
		self.token_decrypted = cipher.decrypt(data)

		#with open('fapi_tolen2.txt', 'w') as fp:
		#	fp.write('\n'.join('%s %s' % x for x in encrypted))
		#with open('tuple_file3.txt', 'w') as file:
		#	file.write(str(encrypted[0][0]))

	def	validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. FS does not support transaction in currency '{0}'"
				).format(currency)
			)
