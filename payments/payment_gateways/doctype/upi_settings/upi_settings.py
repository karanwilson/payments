# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method
from frappe.integrations.utils import create_request_log
from payments.utils import create_payment_gateway

import requests, json


class UPISettings(Document):
	supported_currencies = ["INR"]

	push_txs_url = "https://iciciapi.lyra-network.in/erpservice/ERP/PushTxn"
	check_status_url = "https://iciciapi.lyra-network.in/erpservice/ERP/CheckStatus"
	callback_check_status_url = "https://iciciapi.lyra-network.in/erpservice/ERP/CallbackStatusCheck"


	def	validate(self):
		create_payment_gateway("UPI")
		call_hook_method("payment_gateway_enabled", gateway="UPI")
		#if not self.flags.ignore_mandatory:
		#	self.validate_upi_credentials()

	def	validate_upi_credentials(self):
		pass

	def	validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. FS does not support transaction in currency '{0}'"
				).format(currency)
			)


	def checkStatus():
		pass


	def pushTxn():
		pass


	def cancelTxn():
		pass


	def callback():
		pass


	def CheckCallbackStatus():
		pass