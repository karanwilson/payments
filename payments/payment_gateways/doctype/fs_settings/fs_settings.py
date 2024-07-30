# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method
from payments.utils import create_payment_gateway

from zeep import Client #, Settings

class FSSettings(Document):
	supported_currencies = ["INR"]

	def	validate(self):
		create_payment_gateway("FS")
		call_hook_method("payment_gateway_enabled", gateway="FS")
		if not self.flags.ignore_mandatory:
			self.validate_fs_credentials()
	
	def	validate_fs_credentials(self):
		# login to SOAP Server
		res = self.fapi_login()
		self.login_status = res["Result"]
		if self.login_status == "OK":
			#frappe.throw(res["Result"])
			pass

	def fapi_login(self):
		# create the Zeep SOAP client object
		strPID = self.fs_user
		strPassword = self.get_password(fieldname="fs_password", raise_exception=False)
		fs_client = Client("assets/payments/FS.wsdl")
		return fs_client.service.login(strPID, strPassword)

	def	validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. FS does not support transaction in currency '{0}'"
				).format(currency)
			)
