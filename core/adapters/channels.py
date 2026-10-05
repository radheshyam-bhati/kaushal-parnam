"""Channel adapters. Every one is a demo stub with a visible badge.

docs/03-APP-FLOW.md "Integration Classification (demo)" and docs/02-TRD.md §12:

    WhatsApp   -> SANDBOX   (Meta sandbox number; no live credentials used)
    SMS        -> STUB      (Twilio trial shape; DLT-aware, not registered)
    IVR        -> STUB      (recorded prompts + DTMF capture, no speech-to-text)

No adapter performs a network call. Swapping in a live feed means replacing one
class body, nothing else (docs/08-Architecture.md §4.9).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class SendResult:
    ok: bool
    channel: str
    demo_label: str
    detail: str = ''
    payload: dict | None = None


class BaseChannel:
    channel_code = ''
    demo_label = 'STUB'
    display_name = ''

    def send(self, *, recipient: str, template: str, context: dict) -> SendResult:
        raise NotImplementedError


class WhatsAppChannel(BaseChannel):
    """Meta WhatsApp Business API, SANDBOX. Templates are utility category."""

    channel_code = 'WA'
    demo_label = 'SANDBOX'
    display_name = 'WhatsApp form'

    TEMPLATE = (
        'Hello {name}, this is a follow-up from {centre} about your course '
        '{course}. Reply with your current status: '
        '1=Employed, 2=Self-employed, 3=Apprenticeship, 4=Further study, '
        '5=Not working and looking, 6=Not working and not looking. '
        '{link}'
    )

    def send(self, *, recipient: str, template: str, context: dict) -> SendResult:
        body = template.format(**context)
        return SendResult(
            ok=True,
            channel=self.channel_code,
            demo_label=self.demo_label,
            detail=f'WhatsApp form sent to {recipient}',
            payload={
                'template_category': 'UTILITY',
                'to': recipient,
                'body': body,
                'buttons': ['1', '2', '3', '4', '5', '6'],
            },
        )


class SmsChannel(BaseChannel):
    """SMS nudge with a web link. DLT-ready; DLT registration is out of scope."""

    channel_code = 'SMS'
    demo_label = 'STUB'
    display_name = 'SMS nudge'

    def send(self, *, recipient: str, template: str, context: dict) -> SendResult:
        return SendResult(
            ok=True,
            channel=self.channel_code,
            demo_label=self.demo_label,
            detail=f'SMS nudge sent to {recipient}',
            payload={
                'dlt_entity_id': 'NOT-REGISTERED',
                'to': recipient,
                'body': template.format(**context),
            },
        )


class IvrChannel(BaseChannel):
    """Recorded Marathi/Hindi prompts with keypad DTMF capture. No speech-to-text."""

    channel_code = 'IVR'
    demo_label = 'STUB'
    display_name = 'IVR call'

    PROMPTS = {
        'mr': 'आपले PMKVY कोर्स पूर्ण झाले आहे. तुमच्या रोजगाराची स्थिती काय आहे?',
        'hi': 'आपका पीएमकेवीई कोर्स पूरा हो गया है. आपका रोजगार क्या हाल में है?',
        'en': 'Your PMKVY course is complete. What is your current work status?',
    }

    MENU = {
        '1': 'wage_employment',
        '2': 'self_employment',
        '3': 'apprenticeship',
        '4': 'further_study',
        '5': 'not_working_looking',
        '6': 'not_working_not_looking',
    }

    def send(self, *, recipient: str, template: str, context: dict) -> SendResult:
        language = context.get('language', 'en')
        return SendResult(
            ok=True,
            channel=self.channel_code,
            demo_label=self.demo_label,
            detail=f'IVR call stub for {recipient}',
            payload={
                'language': language,
                'prompt': self.PROMPTS.get(language, self.PROMPTS['en']),
                'dtmf_menu': self.MENU,
                'recorded': True,
            },
        )


class OfficerChannel(BaseChannel):
    """Assisted follow-up: no channel, a task for the district officer."""

    channel_code = 'OFFICER'
    demo_label = 'SIMULATED'
    display_name = 'Officer assisted call'

    def send(self, *, recipient: str, template: str, context: dict) -> SendResult:
        return SendResult(
            ok=True,
            channel=self.channel_code,
            demo_label=self.demo_label,
            detail=f'Officer task queued for {context.get("utid", recipient)}',
            payload={'task': 'CALL'},
        )


CHANNELS = {
    channel.channel_code: channel()
    for channel in (WhatsAppChannel, SmsChannel, IvrChannel, OfficerChannel)
}