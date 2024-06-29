import 'dart:core';
import 'package:cloud_firestore/cloud_firestore.dart';
import 'package:firebase_auth/firebase_auth.dart';
import 'package:logging/logging.dart' show Logger;

final logger = Logger('login_signup');

enum AuthExceptionCode implements Comparable<AuthExceptionCode> {
  invalidCredential(value: 'invalid-credential'),
  invalidEmail(value: 'invalid-email'),
  unknown(value: 'unknown');

  const AuthExceptionCode({required this.value});

  final String value;

  @override
  int compareTo(AuthExceptionCode other) {
    return value.compareTo(other.value);
  }
}

class AuthException implements Exception {
  final String message;
  final Exception cause;
  final AuthExceptionCode code;

  AuthException({
    required this.message,
    required this.cause,
    this.code = AuthExceptionCode.unknown
  });

  @override
  String toString() {
    return message;
  }
}

/// Log into existing user account with email and password.
Future<void> loginWithEmail(String email, String password) async {
  logger.info('log into existing user in FirebaseAuth');
  try {
    await FirebaseAuth.instance.signInWithEmailAndPassword(
      email: email, 
      password: password
    );
    
    logger.info('user login passed');
  }
  on FirebaseAuthException catch (e) {
    // TODO move firebase auth exception codes to shared location
    if (e.code == AuthExceptionCode.invalidCredential.value) {
      throw AuthException(
        message: 'Email or password is incorrect.', 
        cause: e, 
        code: AuthExceptionCode.invalidCredential
      );
    }
    else if (e.code == AuthExceptionCode.invalidEmail.value) {
      throw AuthException(
        message: 'Acount for given email not found.',
        cause: e,
        code: AuthExceptionCode.invalidEmail
      );
    }
    else {
      throw AuthException(
        message: 'Login failed.',
        cause: e
      );
    }
  }
}

/// Register new user account with email and password.
Future<void> registerWithEmail(String email, String password) async {
  logger.info('create new user in FirebaseAuth');
  try {
    UserCredential userCredential = await FirebaseAuth.instance.createUserWithEmailAndPassword(
      email: email, 
      password: password
    );
    String userId = userCredential.user!.uid;

    // Create new User on Cloud Firestore
    await FirebaseFirestore.instance
    // TODO move db identifiers (tables, names) to shared location
    .collection('User')
    .add({
      'email': email, 
      'vaultedId': userId
    });

    logger.info('user signup passed');
    // TODO navigate to profile questions onboarding page
  }
  on FirebaseAuthException catch (e) {
    throw AuthException(
      message: 'Register failed at auth.', 
      cause: e
    );
  }
  on FirebaseException catch (e) {
    throw AuthException(
      message: 'Register failed.', 
      cause: e
    );
  }
}