from enum import Enum
import os
import json
import time
import logging.config
from tenacity import retry, wait_fixed, stop_after_attempt

from attrs import define, field, NOTHING
from typing import List, Callable, Union, Dict, Literal

import google
import google.generativeai as genai
from google.ai.generativelanguage_v1beta.types.content import FunctionCall
from google.protobuf.struct_pb2 import Struct

from SearchClient import SearchClient
from database.FirebaseClient import FirebaseClient
from database.Mission import MissionPeriodType, Mission

GEMINI_MODEL:dict = {
    "flash": "gemini-1.5-flash" 
}

@define
class GeminiClient:
    '''Gemini API client. 

    - This class focuses on communications with the Gemini API and prompt engineering. 
    - It may use other tools (such as SearchClient) to enrich the prompts sent to the Gemini API. 
    '''

    # input params
    gemini_api_key: str = field(default=NOTHING, eq=False)
    tavily_api_key:str = field(default=None, eq=False)
    model: str = field(default="flash", eq=str.lower)
    
    # internal
    logger: logging.Logger = field(init=False)
    client: Union[genai.GenerativeModel, None] = field(init=False) # original gemini client
    search_client: SearchClient = field(init=False)
    fb_client: FirebaseClient = field(init=False)
    tools: List[Callable] = field(factory=list)
    tools_dict: dict = field(factory=dict)

    def __attrs_post_init__(self):
        ''' Run right after __init__() '''
        # initialize logger
        self.logger = logging.getLogger("proact.gemini_client")

        # initialize search client
        if self.tavily_api_key is None:
            self.logger.warning("Tavily API Key not provided, internet search tool will not be available.")
        else:
            self.search_client = SearchClient(api_key=self.tavily_api_key)
            self.logger.info('tavily api enabled for internet search tool.')
            self.add_tool_to_toolbox(self.internet_search_tool, "internet_search_tool")
        
        # initialize firebase client
        self.fb_client = FirebaseClient()

        # initialize gemini client
        genai.configure(api_key=self.gemini_api_key)
        self.client = genai.GenerativeModel(
            model_name = GEMINI_MODEL[self.model],
            tools = self.tools # may be empty
        )
        self.logger.info("Gemini client initialized")
    # end def

    def get_new_missions_for_user(
        self, 
        user_id: str, 
        mission_type: MissionPeriodType,
        num_missions: int = 3,
        attempt_max: int = 5,
        debug = False # if true, do not populate missions to db
    ) -> List[dict]:
        '''
        Retrieve information about an user, then generate `num_missions` of `mission_type` missions in JSON format.
        '''

        self.logger.info(f"Received request to generate {num_missions} '{mission_type.name}' missions.")
        
        # retrieve user information
        user = self.fb_client.get_user_by_id(user_id)
        personal_info = {
            "location": user['location'],
            "occupation": user['occupation']
        }
        interests = user['interests']
        past_missions = self._get_user_past_missions_as_strs(user_id)

        # generate new missions
        valid_missions_generated = False
        attempt = 0
        missions: List[Mission]
        while not valid_missions_generated and attempt < attempt_max:
            attempt += 1
            self.logger.info(f'generate missions attempt {attempt}')

            if mission_type == MissionPeriodType.WEEK:
                new_missions_str = self._generate_missions(
                    num_missions=num_missions,
                    personal_info=personal_info,
                    interests=interests,
                    past_missions=past_missions,
                    mission_period='THIS WEEK',
                    mission_period_emphasis='(Important) Be easy and straight-forward enough for me to finish in a week.',
                    impact_qualifier='Determine the environmental problems that I can make an impact in.'
                )
            elif mission_type == MissionPeriodType.ONGOING:
                new_missions_str = self._generate_missions(
                    num_missions=num_missions,
                    personal_info=personal_info,
                    interests=interests,
                    past_missions=past_missions,
                    mission_period='for the next few months',
                    mission_period_emphasis='Be detailed and big enough for me to work on for a few months.',
                    impact_qualifier='Focus on one or a few most critical environmental problems that I can make an impact in.'
                )
            else:
                msg = f"mission type must be one of {MissionPeriodType._member_names_}"
                self.logger.error(msg)
                raise ValueError(msg)
                
            # missions parsing check
            try:
                missions = self._parse_missions(new_missions_str)
                valid_missions_generated = True
            
            except json.decoder.JSONDecodeError:
                self.logger.warning(f"Error parsing missions from gemini answer")
        # end while not generated
        
        # add new missions to db
        for mission in missions:
            self.fb_client.add_mission_to_db(
                mission=mission,
                user_id=user_id,
                debug = debug
            )
        # end for

        self.logger.info(f"Successfully generated {len(missions)} missions for user username={user['username']}.")
        return missions
    # end def

    def _parse_missions(self, missions_str: str) -> List[Mission]:
        '''Parse missions from gemini response.

        :raises: `JsonDecodeError` on parse failure.
        '''

        if '```json' in missions_str: # common error 
            missions_str = missions_str.replace('```json', '').replace('```', '')
        return json.loads(missions_str, object_hook=Mission.from_dict)
    # end def

    # @retry(wait=wait_fixed(2), stop=stop_after_attempt(3))
    def _submit_prompt(self, prompt:str) -> str:
        '''
        Submit a regular prompt to the Gemini API. Function calling is automatic but is implemented manually.
        '''
        self.logger.debug(f"Received prompt: {prompt}")
        start_time = time.perf_counter()
        
        # format message and send to gemini api
        messages = [
            {"role": "user", "parts": [prompt]}
        ]

        answer_available:bool = False # true if not more tool call is requested
        max_depth = 5 # max number of prompt submissions until an answer is available
        cur_depth = 0

        while not answer_available: # keep submitting new prompt until an answer is available
            cur_depth += 1
            if cur_depth > max_depth:   # possibility of infinite loop
                msg = f"Max prompt submission depth of {max_depth} reached."
                self.logger.error(msg)
                raise RuntimeError(msg)
            
            try: # sending a prompt to the Gemini API
                response = self.client.generate_content(messages)
                answer_available = True
            except google.api_core.exceptions.InvalidArgument as e:# usually for invalid API key
                self.logger.error(f"Error occured when sending prompt to Gemini API: {e}")
                raise google.api_core.exceptions.InvalidArgument(e)

            # check for tool call request
            for part in response.parts:
                if part.function_call:  # tool call requested
                    answer_available = False
                    tool_output = self._execute_tool(part.function_call)
                    messages.append( # Need to keep track of conversation manually
                        {"role": "model", "parts": response.parts},
                    )
                    
                    # Put the result in a protobuf Struct
                    tool_response = Struct()
                    tool_response.update({"result": tool_output})

                    # Create a function_response part
                    function_response = genai.protos.Part(
                        function_response=genai.protos.FunctionResponse(
                            name=part.function_call.name, 
                            response=tool_response
                        )
                    )

                    # Append tool_call output to messages
                    messages.append(
                        {"role": "user", "parts": [function_response]}
                    )
                    # May need to loop until there is no fn call since the API may request many function calls in a row
                # end if function call
            # end for part in response
        # end while no answer

        # Get final response's text  
        response_text = response.text  

        # get elapsed time and log answer
        elapsed_time = time.perf_counter() - start_time
        self.logger.info(f"Answer generated in ({elapsed_time:.2f}s)")
        self.logger.debug(f"Answer: {response_text}")

        return response_text
    # end def

    def _get_user_past_missions_as_strs(self, user_id:str) -> List[str]:
        '''
        Get and format past missions of user `user_id` as a single string, ready to be used in prompts.
        
        TODO omit child missions?
        '''

        past_missions = self.fb_client.get_user_past_missions(user_id, depth=1)
        self.logger.info(f"Found {len(past_missions)} past missions for user with id {user_id}")

        if len(past_missions) == 0:
            return ["There has not been any missions in the past."]
        past_missions_as_strs = []
        for i, mission in enumerate(past_missions):
            mission_str = f"{i+1}. {mission.title}"   # each mission starts with a number
            for step in mission.steps_mission:          # each step starts with "-"
                mission_str += '\n' + f"- {step.title}"
            past_missions_as_strs.append(mission_str)

        return past_missions_as_strs
    # end def

    # TODO: Implement this function to use gemini ChatSession with automatic function calling
    @retry(wait=wait_fixed(2), stop=stop_after_attempt(3))
    def _submit_chat(self, msg:str) -> str:
        '''
        Initiate a chat session and send a chat to the Gemini API. Function calling is automatic by default.
        '''
        chat = self.client.start_chat(enable_automatic_function_calling=True)
        chat.send_message(msg)
        pass

    def _generate_missions(
        self,
        num_missions: int,
        personal_info: dict,
        interests: List[str],
        past_missions: List[str],
        mission_period: str,
        mission_period_emphasis: str,
        impact_qualifier: str,
        title_word_limit: int = 25,
        description_word_limit: int = 100
    ):
        '''General mission generation method to submit prompt.

        TODO was `impact_qualifier` supposed to be uniform? If so, move to arg default.
        TODO the emphasis on "do an internet search" is not enough to guaranteed usage of the tool. Perform internet search another way.

        :param mission_period: Phrase to describe timeline scope/period estimated to complete a mission.
        :param mission_period_emphasis: Sentence to emphasize how suggested missions should adapt to the requested period.
        :param impact_qualifier: Sentence to emphasize selection of environmental impact, informed by mission period.
        '''

        # Info formatted as str
        personal_info_str = '\n'.join([f'- {k}: {v}' for k,v in personal_info.items()])
        interest_info_str = '\n'.join([f'- {item}' for item in interests])
        past_missions_str = '\n'.join(past_missions)

        # Prompt template
        prompt = f'''
        Your goal is to suggest {num_missions} missions for me to do {mission_period} to help the environment and reduce global warming. 

        Note that each mission should:
        - Be clear enough for me to keep track of my progress.
        - {mission_period_emphasis} 
        - Be personalized to my personal information and interests listed below. 
        - Focus on the environmental problems near my location.

        The steps you should take:
        1. (IMPORTANT) Do an internet search for environmental problem near my location. 
        2. {impact_qualifier}
        3. Devise a set of missions {num_missions} for me to do with a clear description why each mission is important, is relevant (and perhaps even helpful) to me, and clear steps for me to take.  

        MAKE SURE to structure your answer in the following JSON format and do not add "```json" in the beginning:

        [   // a list of missions as json objects
            {{
                "title": // title of the mission, in {title_word_limit} words or fewer
                "description": // description of the mission, in {description_word_limit} words or less
                "steps": [
                    // an array of steps as strings each of {description_word_limit} words or less
                ]
            }}
        ]

        Personal information:
        {personal_info_str}

        My Interests:
        {interest_info_str}

        Below are some missions you have given me in the past, try to generate new missions that are different than these:
        {past_missions_str}
        '''

        # Submit prompt
        missions_str = self._submit_prompt(prompt)

        return missions_str
    # end def

    # Gemini Tool 
    def internet_search_tool(self, query:str) -> str:
        '''
        Perform an internet search given a query. 
        
        Args:
            self: ignore this parameter
            query (str): A clear and concise query
        '''
        start_time = time.perf_counter()
        self.logger.info(f'Internet search requested with query: "{query}"')

        if not hasattr(self, 'search_client'):
            error_msg = 'Search client was not initialized'
            self.logger.error(error_msg)
            raise RuntimeError(error_msg)
        
        # just do a quick qna search for now
        result = self.search_client.quick_search(
            query=query, 
            search_depth='advanced'
        )

        # get elapsed time 
        elapsed_time = time.perf_counter() - start_time
        self.logger.info(f"Search result obtained in ({elapsed_time:.2f}s)")
        self.logger.debug(f"Search result: {result}")

        # make sure result is of type str
        if not isinstance(result, str):
            error_msg = f'Expect searchr result to be of type `str`, got `{type(result)}` instead.'
            self.logger.error(error_msg)
            raise ValueError(error_msg)
    
        return result
    # end def

    def add_tool_to_toolbox(self, tool: Callable, tool_name:str) -> None:
        '''
        Add a tool (function) to a list (for the gemini api) and also a dict (for executing those tools) of tools
        '''
        self.tools.append(tool)
        self.tools_dict[tool_name] = tool
        self.logger.info(f'Tool `{tool_name}` added to toolbox')
    # end def

    def _execute_tool(self, tool_call: FunctionCall) -> str:
        '''
        Execute the tool requested by Gemini, output should always be of type `str`
        '''
        # get tool call details
        tool_name = tool_call.name
        tool_args = tool_call.args
        self.logger.info(f"Tool call requested for `{tool_name}` with params = {dict(tool_args)}")
        
        # execute the tool
        tool_output = self.tools_dict[tool_name](**tool_args) # pass the params to actual function
        return tool_output
    # end def
# end class

# test driver
if __name__ == "__main__":
    from dotenv import load_dotenv
    from utils.init_logging import init_logging, set_global_logging_level
    load_dotenv()
    init_logging()

    # Set the level of all loggers
    set_global_logging_level(logging.DEBUG)

    # Initiate gemini client
    client = GeminiClient(
        gemini_api_key=os.getenv("GEMINI_API_KEY"),
        tavily_api_key=os.getenv("TAVILY_API_KEY")
    )

    debug_mode = os.getenv('DEBUG_MODE', 'True').lower().strip() == 'true'
    client.logger.debug(f'debug_mode={debug_mode}')

    # client._submit_prompt("What are some trending cookie recipes on the internet? Provide detaisl on how to make one of them.")

    saved_gemini_answer_missions = os.getenv('SAVED_GEMINI_ANSWER_MISSIONS')
    if saved_gemini_answer_missions is not None:
        client.logger.info(f'parse missions from saved answer {saved_gemini_answer_missions}')
        with open(saved_gemini_answer_missions, mode='r') as f:
            missions = client._parse_missions(f.read())

            client.logger.info(f'missions[0]={missions[0]}')
            client.logger.info(f'missions[0].steps[0]={missions[0].steps_mission[0]} id={missions[0].steps_id[0]}')

            client.fb_client.add_mission_to_db(
                mission=missions[0],
                user_id=os.getenv('USER_ID'),
                debug=debug_mode
            )

            client.logger.debug(f"Generated missions: \n {json.dumps(
            [
                mission.to_dict(depth=1)
                for mission in missions
            ], 
            indent=4
        )}")
        # end with
    # end if saved answer
    else:
        client.logger.info(f'generate new missions without saved answer')
        # Try get new ongoing missions
        client.get_new_missions_for_user(
            mission_type=MissionPeriodType.ONGOING,
            user_id=os.getenv('USER_ID'),
            num_missions=2,
            debug=debug_mode
        )
    # end if not saved answer
# end if __main__
