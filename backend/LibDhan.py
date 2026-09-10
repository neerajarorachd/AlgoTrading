from Dhan_Tradehull import Tradehull
from LibEnum import TradingEnums as TE
from LibDBOps import DBOps


class Dhan:

   from Dhan_Tradehull import Tradehull
from LibDBOps import DBOps
from LibEnum import TradingEnums as TE


class Dhan:

    def __init__(
        self,
        token_id: int = None,
        account_id: str = None,
        token_type: TE.TokenType = None
    ):

        dbo = DBOps()

        # ---------------------------------------
        # 🔐 Validation
        # ---------------------------------------
        if token_id is None and account_id is None:
            raise ValueError(
                "Either token_id or account_id must be provided"
            )

        # ---------------------------------------
        # 1️⃣ Fetch Token
        # ---------------------------------------
        token_data = dbo.GetActiveToken(
            token_id=token_id,
            account_id=account_id,
            token_type=token_type.value if token_type is not None else None
        )

        if token_data is None:
            raise Exception(
                f"No active token found (token_id={token_id}, account_id={account_id}, token_type={token_type})"
            )

        # ---------------------------------------
        # 2️⃣ Fetch Broker Account
        # ---------------------------------------
        account_data = dbo.GetBrokerAccount(token_data["AccountID"])

        if account_data is None:
            raise Exception(
                f"No broker account found for {token_data['AccountID']}"
            )

        client_id = account_data["ClientID"]
        access_token = token_data["AccessToken"]

        # ---------------------------------------
        # 3️⃣ Create Tradehull Connection
        # ---------------------------------------
        self._Connection = Tradehull(client_id, access_token)

        # ---------------------------------------
        # Optional Tracking (future use)
        # ---------------------------------------
        self._TokenID = token_data["TokenID"]
        self._AccountID = token_data["AccountID"]

    # ---------------------------------------
    # Connection Getter
    # ---------------------------------------
    @property
    def DhanConnection(self):
        return self._Connection


######--Hardcoded Tokens have been replaced with a db call --######

# from Dhan_Tradehull import Tradehull
# from LibEnum import TradingEnums as TE

# str_Client_Code         =   "1106451789"
# str_Token_ID_Global     =   "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiIiwiZXhwIjoxNzcwOTYwMTk3LCJpYXQiOjE3NzA4NzM3OTcsInRva2VuQ29uc3VtZXJUeXBlIjoiU0VMRiIsIndlYmhvb2tVcmwiOiIiLCJkaGFuQ2xpZW50SWQiOiIxMTA2NDUxNzg5In0.dUKInPMJ0MOA8EhjCQEsiECRfPVhPIOiyd6Qk2BGq0tlmGkFo395yty-7L_bw4u6V8xJ0ka52ZzeLzY15AlH4w"
# str_Token_ID_StrategyA  =   "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiU3RyYXRlZ3lBLUV4YW1wbGUiLCJleHAiOjE3Njc5MzQ4OTksImlhdCI6MTc2Nzg0ODQ5OSwidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6IiIsImRoYW5DbGllbnRJZCI6IjExMDY0NTE3ODkifQ.QEQzQVrByM62clxndQtOr6AoT2kS6YkCVFvBwujjqAXPBZJAdAuV1qTFDEfIalRoFzX-NA9ouR8QMMNKprt3SA"
# str_Token_ID_StrategyB  =   "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiU3RyYXRlZ3lBLUV4YW1wbGUiLCJleHAiOjE3Njc5MzQ4OTksImlhdCI6MTc2Nzg0ODQ5OSwidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6IiIsImRoYW5DbGllbnRJZCI6IjExMDY0NTE3ODkifQ.QEQzQVrByM62clxndQtOr6AoT2kS6YkCVFvBwujjqAXPBZJAdAuV1qTFDEfIalRoFzX-NA9ouR8QMMNKprt3SA"
# str_Token_ID_StrategyC  =   "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiU3RyYXRlZ3lBLUV4YW1wbGUiLCJleHAiOjE3Njc5MzQ4OTksImlhdCI6MTc2Nzg0ODQ5OSwidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6IiIsImRoYW5DbGllbnRJZCI6IjExMDY0NTE3ODkifQ.QEQzQVrByM62clxndQtOr6AoT2kS6YkCVFvBwujjqAXPBZJAdAuV1qTFDEfIalRoFzX-NA9ouR8QMMNKprt3SA"
# str_Token_ID_StrategyD  =   "eyJ0eXAiOiJKV1QiLCJhbGciOiJIUzUxMiJ9.eyJpc3MiOiJkaGFuIiwicGFydG5lcklkIjoiU3RyYXRlZ3lBLUV4YW1wbGUiLCJleHAiOjE3Njc5MzQ4OTksImlhdCI6MTc2Nzg0ODQ5OSwidG9rZW5Db25zdW1lclR5cGUiOiJTRUxGIiwid2ViaG9va1VybCI6IiIsImRoYW5DbGllbnRJZCI6IjExMDY0NTE3ODkifQ.QEQzQVrByM62clxndQtOr6AoT2kS6YkCVFvBwujjqAXPBZJAdAuV1qTFDEfIalRoFzX-NA9ouR8QMMNKprt3SA"

# class Dhan:
#     """ def __new__(cls):
#         tsl     =   Tradehull(str_Client_Code,str_Token_ID)
#         return tsl """
    

#     def __init__(self, token_type: TE.TokenType = TE.TokenType.GLOBAL):
#         self._Connection = Tradehull(str_Client_Code, self.GetToken(token_type))

#     @property
#     def DhanConnection(self):
#         return self._Connection
    
#     _TOKEN_MAP = {
#     TE.TokenType.GLOBAL:    str_Token_ID_Global,
#     TE.TokenType.STRATEGYA: str_Token_ID_StrategyA,
#     TE.TokenType.STRATEGYB: str_Token_ID_StrategyB,
#     TE.TokenType.STRATEGYC: str_Token_ID_StrategyC,
#     TE.TokenType.STRATEGYD: str_Token_ID_StrategyD,
#     }

#     def GetToken(self, token_type: TE.TokenType):
#         return self._TOKEN_MAP.get(token_type, str_Token_ID_Global)
    
