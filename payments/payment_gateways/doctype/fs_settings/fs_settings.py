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
#from Crypto.Util.Padding import pad, unpad
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
		login_res = self.fapi_login()
		self.login_status = login_res["Result"]
		if self.login_status == "OK":
			self.last_login_at = datetime.now()
		self.request_transfer_token()

	#@frappe.whitelist(allow_guest=True)
	def fapi_login(self):
		# Initialise the SOAP client
		global fs_client
		fs_client = Client("assets/payments/FS.wsdl")

		strPID = self.fs_user
		strPassword = self.get_password(fieldname="fs_password", raise_exception=False)

		login_res = fs_client.service.login(strPID, strPassword)
		return login_res

	def request_transfer_token(self):
		param = [{"Req":""}]
		token_res = fs_client.service.requestTransferToken(param)
		#self.token_response = token_res
		encrypted = urldecode(token_res)
		#self.token_encrypted = encrypted[0][0]
		d = encrypted[0][0].split(";")
		#self.encrypt_data = d[0]
		#self.encrypt_iv = d[1]
		key = "fstockencryptkey".encode("utf8")
		data = b64decode(d[0])
		iv = b64decode(d[1])
		
		decipher = AES.new(key, AES.MODE_CBC, iv)
		result = decipher.decrypt(data)

		transfer_token = result.decode("ascii").strip().strip('\x00')
		"""
		https://stackoverflow.com/questions/38883476/how-to-remove-those-x00-x00

		NUL chars are not treated as whitespace by default by strip(), and as such you need to specify explicitly.
		This can catch you out, as print() will of course not show the NUL chars. 
		My solution that I used was to clean the string using ".strip().strip('\x00')
		"""
		self.token_decrypted = transfer_token
		#with open('fapi_token5.txt', 'w') as file:
		#	file.write(str(transfer_token))
		#with open('fapi_token2.txt', 'w') as fp:
		#	fp.write('\n'.join('%s %s' % x for x in transfer_token))

		return transfer_token

	def	validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. FS does not support transaction in currency '{0}'"
				).format(currency)
			)


@frappe.whitelist(allow_guest=True)
def add_transfer(doc, method):
	fs_controller = frappe.get_doc("FS Settings")
	# Add a login timeout check here
	login_res = fs_controller.fapi_login()

	if login_res["Result"] == "OK":
		transfer_token = fs_controller.request_transfer_token()
		if (transfer_token):

			strAccountNumberFrom = frappe.get_value("Customer", doc.party, "custom_fs_account_number")
			param = {
				"strAccountNumberFrom": strAccountNumberFrom,
				"strAccountNumberTo": doc.custom_fs_account_to,
				"fAmount": doc.paid_amount,
				"strDescription": doc.custom_contribution_type,
				#"check": "Yes",
				#"token": transfer_token
			}
			#with open('fapi_token8.txt', 'w') as file:
			#	file.write(str(param))
			#addTransfer_res = fs_client.service.addTransfer(param)
			#frappe.throw(addTransfer_res)
			testTransfer_res = fs_client.service.testTransfer(param)
			frappe.throw(testTransfer_res)
