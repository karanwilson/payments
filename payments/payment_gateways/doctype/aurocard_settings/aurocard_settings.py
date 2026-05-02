# Copyright (c) 2024, Frappe Technologies and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import call_hook_method
from frappe.integrations.utils import create_request_log
from payments.utils import create_payment_gateway
import json

from zeep import Client #, Settings
from base64 import b64decode
from oauthlib.common import urldecode
from Crypto.Cipher import AES
from datetime import datetime


class AurocardSettings(Document):
	supported_currencies = ["INR"]

	def	validate(self):
		create_payment_gateway("Aurocard")
		call_hook_method("payment_gateway_enabled", gateway="Aurocard")
		if not self.flags.ignore_mandatory:
			self.validate_aurocard_credentials()

	def	validate_aurocard_credentials(self):
		pass

	def	validate_transaction_currency(self, currency):
		if currency not in self.supported_currencies:
			frappe.throw(
				_(
					"Please select another payment method. FS does not support transaction in currency '{0}'"
				).format(currency)
			)
