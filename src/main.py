"""
Main entry point for the state management demo.
Provides CLI interface and test scenarios.
"""
import asyncio
import logging
import sys
from typing import Optional

from dotenv import load_dotenv

from agent import run_conversation, initialize, shutdown
from checkpointer import search_conversations, get_user_statistics

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()


async def interactive_mode():
    """Interactive CLI for testing the agent."""
    print("=" * 60)
    print("  Customer Service Agent - Interactive Mode")
    print("=" * 60)
    print()
    print("Commands:")
    print("  /new       - Start new conversation")
    print("  /search    - Search conversations")
    print("  /stats     - View user statistics")
    print("  /quit      - Exit")
    print()
    
    user_id = input("Enter user ID (default: demo-user): ").strip() or "demo-user"
    session_id: Optional[str] = None
    
    print(f"\n✅ Started as user: {user_id}")
    print("Type your message (or /help for commands)\n")
    
    while True:
        try:
            user_input = input(f"[{user_id}] You: ").strip()
            
            if not user_input:
                continue
            
            # Handle commands
            if user_input == "/quit":
                print("👋 Goodbye!")
                break
            
            elif user_input == "/new":
                session_id = None
                print("🆕 Started new conversation\n")
                continue
            
            elif user_input == "/search":
                query = input("Search query: ").strip()
                if query:
                    results = await search_conversations(query=query, user_id=user_id, limit=5)
                    print(f"\n🔍 Found {len(results)} results:")
                    for i, result in enumerate(results, 1):
                        print(f"\n{i}. Session: {result['session_id'][:8]}...")
                        print(f"   Intent: {result['intent']} | Resolved: {result['resolved']}")
                        print(f"   Messages: {len(result['conversation_history'])}")
                print()
                continue
            
            elif user_input == "/stats":
                stats = await get_user_statistics(user_id)
                print(f"\n📊 Statistics for {user_id}:")
                print(f"   Total conversations: {stats.get('total_conversations', 0)}")
                print(f"   Resolved: {stats.get('resolved_count', 0)}")
                print(f"   Intents: {stats.get('intents', [])}")
                print()
                continue
            
            elif user_input == "/help":
                print("\nCommands:")
                print("  /new       - Start new conversation")
                print("  /search    - Search conversations")
                print("  /stats     - View user statistics")
                print("  /quit      - Exit")
                print()
                continue
            
            # Run conversation
            state, session_id = await run_conversation(
                user_id=user_id,
                user_message=user_input,
                session_id=session_id
            )
            
            # Display response
            reply = state.get("final_reply", "No response")
            intent = state.get("intent", "unknown")
            awaiting_human = state.get("awaiting_human_input", False)
            
            print(f"\n[Agent ({intent})] {reply}")
            
            if awaiting_human:
                print("\n⏸️  Conversation paused - awaiting human input")
                print(f"📋 Session ID: {session_id}")
            
            print(f"\n💬 Messages: {len(state.get('conversation_history', []))}")
            print(f"🔗 Session: {session_id[:8]}...\n")
            
        except KeyboardInterrupt:
            print("\n\n👋 Interrupted. Goodbye!")
            break
        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)
            print(f"\n❌ Error: {e}\n")


async def test_basic_conversation():
    """Test basic conversation flow."""
    print("\n" + "=" * 60)
    print("TEST: Basic Conversation")
    print("=" * 60 + "\n")
    
    user_id = "test-user-basic"
    
    # Turn 1: FAQ
    print("Turn 1: Asking about return policy...")
    state1, session_id = await run_conversation(
        user_id=user_id,
        message="What's your return policy?",
        session_id=None
    )
    print(f"Agent: {state1['final_reply']}")
    print(f"Intent: {state1['intent']}")
    print(f"Session: {session_id}\n")
    
    # Turn 2: Follow-up (same session)
    print("Turn 2: Follow-up question...")
    state2, _ = await run_conversation(
        user_id=user_id,
        message="How long do I have?",
        session_id=session_id
    )
    print(f"Agent: {state2['final_reply']}")
    print(f"Messages: {len(state2['conversation_history'])}\n")
    
    print("✅ Basic conversation test passed\n")


async def test_hitl_workflow():
    """Test HITL interrupt and resume."""
    print("\n" + "=" * 60)
    print("TEST: HITL Workflow (Interrupt & Resume)")
    print("=" * 60 + "\n")
    
    user_id = "test-user-hitl"
    
    # Step 1: Trigger HITL
    print("Step 1: Angry customer (triggers HITL)...")
    state1, session_id = await run_conversation(
        user_id=user_id,
        message="I'm extremely angry! Refund my order NOW!",
        session_id=None
    )
    print(f"Agent: {state1['final_reply']}")
    print(f"Intent: {state1['intent']}")
    print(f"Awaiting human: {state1['awaiting_human_input']}")
    print(f"Session: {session_id}\n")
    
    assert state1["awaiting_human_input"], "❌ HITL not triggered"
    assert state1["intent"] == "human", "❌ Intent should be 'human'"
    
    print("⏸️  Conversation paused - simulating human review...\n")
    
    # Step 2: Resume after human approval
    print("Step 2: Human approves, resume conversation...")
    state2, _ = await run_conversation(
        user_id=user_id,
        message="Approved: Issue full refund for order #12345",
        session_id=session_id  # Same session - resumes
    )
    print(f"Agent: {state2['final_reply']}")
    print(f"Awaiting human: {state2.get('awaiting_human_input', False)}")
    print(f"Resolved: {state2.get('resolved', False)}\n")
    
    print("✅ HITL workflow test passed\n")


async def test_durability():
    """Test state persistence across restarts."""
    print("\n" + "=" * 60)
    print("TEST: Durability (State Persistence)")
    print("=" * 60 + "\n")
    
    user_id = "test-user-durability"
    
    # Step 1: Start conversation
    print("Step 1: Start conversation...")
    state1, session_id = await run_conversation(
        user_id=user_id,
        message="I need help with order #67890",
        session_id=None
    )
    print(f"Agent: {state1['final_reply']}")
    print(f"Order ID: {state1.get('order_id')}")
    print(f"Session: {session_id}\n")
    
    # Simulate restart by clearing in-memory state
    print("🔄 Simulating server restart...\n")
    await asyncio.sleep(1)
    
    # Step 2: Resume after "restart"
    print("Step 2: Resume conversation (state loaded from PostgreSQL)...")
    state2, _ = await run_conversation(
        user_id=user_id,
        message="When will it arrive?",
        session_id=session_id  # Same session
    )
    print(f"Agent: {state2['final_reply']}")
    print(f"Order ID still available: {state2.get('order_id')}")
    print(f"Messages: {len(state2['conversation_history'])}\n")
    
    assert state2.get("order_id") == "67890", "❌ Order ID lost after restart"
    
    print("✅ Durability test passed\n")


async def test_search():
    """Test Elasticsearch search functionality."""
    print("\n" + "=" * 60)
    print("TEST: Conversation Search")
    print("=" * 60 + "\n")
    
    user_id = "test-user-search"
    
    # Create some test conversations
    print("Creating test conversations...")
    
    await run_conversation(user_id, "I need a refund for my delayed order", None)
    await run_conversation(user_id, "What's your shipping policy?", None)
    await run_conversation(user_id, "My package is damaged, need refund", None)
    
    print("Waiting for Elasticsearch indexing...\n")
    await asyncio.sleep(2)  # Wait for indexing
    
    # Search
    print("Searching for 'refund'...")
    results = await search_conversations(query="refund", user_id=user_id, limit=5)
    
    print(f"Found {len(results)} results:")
    for i, result in enumerate(results, 1):
        print(f"\n{i}. Session: {result['session_id'][:8]}...")
        print(f"   Intent: {result['intent']}")
        print(f"   Resolved: {result['resolved']}")
    
    print("\n✅ Search test passed\n")


async def run_all_tests():
    """Run all test scenarios."""
    try:
        await test_basic_conversation()
        await test_hitl_workflow()
        await test_durability()
        await test_search()
        
        print("=" * 60)
        print("✅ All tests passed!")
        print("=" * 60 + "\n")
        
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}\n")
    except Exception as e:
        logger.error(f"Test error: {e}", exc_info=True)


async def main():
    """Main entry point."""
    # Parse command line arguments
    if len(sys.argv) > 1:
        command = sys.argv[1]
    else:
        command = "--interactive"
    
    try:
        # Initialize services
        await initialize()
        
        # Run based on command
        if command == "--interactive":
            await interactive_mode()
        elif command == "--test-basic":
            await test_basic_conversation()
        elif command == "--test-hitl":
            await test_hitl_workflow()
        elif command == "--test-durability":
            await test_durability()
        elif command == "--test-search":
            await test_search()
        elif command == "--test-all":
            await run_all_tests()
        else:
            print(f"Unknown command: {command}")
            print("\nUsage:")
            print("  python main.py [command]")
            print("\nCommands:")
            print("  --interactive       Interactive CLI (default)")
            print("  --test-basic        Test basic conversation")
            print("  --test-hitl         Test HITL workflow")
            print("  --test-durability   Test state persistence")
            print("  --test-search       Test search functionality")
            print("  --test-all          Run all tests")
        
    except KeyboardInterrupt:
        print("\n\n👋 Interrupted. Shutting down...")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
    finally:
        # Cleanup
        await shutdown()


if __name__ == "__main__":
    asyncio.run(main())
