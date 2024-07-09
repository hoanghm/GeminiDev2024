import 'package:firebase_auth/firebase_auth.dart';
import 'package:flutter/material.dart';
import 'package:gemini_proact_flutter/model/database/user.dart';
import 'package:gemini_proact_flutter/view/Auth/login_signup_page.dart';
import 'package:gemini_proact_flutter/view/Onboarding/onboarding_form.dart';
import 'package:gemini_proact_flutter/view/home/home_page.dart';
import 'package:logging/logging.dart' show Logger;
import 'package:gemini_proact_flutter/model/database/firestore.dart' show getUser;

final logger = Logger((AuthPage).toString());

class AuthPage extends StatefulWidget {
  const AuthPage({super.key});
  @override
  AuthPageState createState() {
    return AuthPageState();
  }
}

class AuthPageState extends State<AuthPage> {
  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: StreamBuilder<User?>(
        stream: FirebaseAuth.instance.authStateChanges(),
        builder: (context, snapshot) {
          if (snapshot.hasData && snapshot.data!.emailVerified) {
            // user logged in and verified
            logger.info('user logged in as name=${snapshot.data?.displayName} email=${snapshot.data?.email} verified=${snapshot.data?.emailVerified}');
            getUser()
              .then((possibleUser) {
                if (possibleUser == null || !possibleUser.onboarded) {
                  logger.info("To onboarding");
                  Navigator.push(
                    context, 
                    MaterialPageRoute(builder: (context) => OnboardingForm(user: possibleUser!))
                  );
                } else {
                  logger.info("To home page");
                  Navigator.push(
                    context, 
                    MaterialPageRoute(builder: (context) => const HomePage())
                  );
                }
              })
              .catchError((e) => throw ErrorDescription('$e'));
            return const Center(
              child: CircularProgressIndicator(),
            );
          }
          else {
            // user not logged in
            logger.info('user not logged in');
            return const LoginSignupPage();
          }
        },
      )
    );
  }
}